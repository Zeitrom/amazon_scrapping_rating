from __future__ import annotations
"""
availability_grade_async.py — v14 (by Zeitrom aka @huve)
• Intégration du cas de disponibilité "No featured offers available" (fodcx).
• Logique de parsing pour la disponibilité et les notes entièrement revue pour plus de robustesse.
• Gère les variations de mise en page et les différentes langues pour les notes.
• Lecture d'un fichier CSV/Excel.
• Scraping Amazon par chunks avec Playwright Chromium (headless).
• Gestion des blocages hiérarchique : Click-Verify puis OCR.
• Gestion fiable du consentement aux cookies.
• Rotation de User-Agent et application de `playwright-stealth`.
• Logs épurés, focalisés sur la progression et les erreurs critiques.
"""

import asyncio
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import List, Tuple, Union

import pandas as pd
from bs4 import BeautifulSoup
from amazoncaptcha import AmazonCaptcha
from playwright.async_api import (
    async_playwright,
    Page,
    Response,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

# ─────────────────────────── CONFIG ──────────────────────────── #
CHUNK_SIZE            = 100
MAX_CONCURRENCY       = 5
NAV_TIMEOUT           = 45_000
WAIT_BETWEEN_CHUNKS   = (10, 20)
WAIT_BETWEEN_REQUESTS = (1, 3)
MAX_RETRIES           = 3
OUTPUT_FILE           = ""
SAMPLE_SIZE           = int(os.getenv("SAMPLE_SIZE", "0"))

# Regex
CAPTCHA_RE = re.compile(r"captcha|robot|verify|attention|vérification", re.IGNORECASE)
NOT_FOUND_RE = re.compile(r"page introuvable|page non trouvée|not found|ne peut pas trouver cette page|désolé", re.IGNORECASE)

# User-Agent
UA_POOL: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
]

# ─────────────────────────── LOGGING ──────────────────────────── #
logger = logging.getLogger("scraper")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)
    file_handler = logging.FileHandler("scraping.log", mode="w", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

# ────────────────────────── HTML PARSERS (FINAL) ─────────────────── #

def extract_availability(soup):
    selectors_to_try = [
        '#availability .a-color-success',
        '#availability .a-color-error',
        '#availability > span:first-of-type',
        '#desktop_buybox_feature_div #availability span',
        '#fodcx_feature_div #fod-cx-message-with-learn-more span:first-child',
        '#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE',
        '#outOfStock span.a-color-price',
        '#outOfStockBuyBox_feature_div span.a-color-price',
    ]
    for selector in selectors_to_try:
        element = soup.select_one(selector)
        if element and (text := element.get_text(strip=True)):
            if "Lire la suite" in text: continue
            return text
    return "N/A"

def extract_reviews_info(soup):
    stars = "Produit avec aucune note"
    reviews_count = "Produit avec aucune note"
    reviews_section = soup.select_one('#averageCustomerReviews_feature_div, #averageCustomerReviews')
    if not reviews_section:
        return stars, reviews_count

    popover = reviews_section.select_one('#acrPopover')
    if popover and popover.has_attr('title'):
        match = re.search(r'([\d,\.]+)\s*(étoiles sur 5|out of 5 stars)', popover['title'].replace(',', '.'))
        if match:
            try:
                stars = float(match.group(1))
            except (ValueError, IndexError):
                pass
    
    if stars == "Produit avec aucune note":
        star_span = reviews_section.select_one('span.a-icon-alt')
        if star_span and (text := star_span.get_text(strip=True)):
             match = re.search(r'([\d,\.]+)', text.replace(',', '.'))
             if match:
                try:
                    stars = float(match.group(1))
                except ValueError:
                    pass

    review_text_element = reviews_section.select_one('#acrCustomerReviewText')
    if review_text_element and (text := review_text_element.get_text(strip=True)):
        digits = re.sub(r'\D', '', text)
        if digits:
            try:
                reviews_count = int(digits)
            except ValueError:
                pass

    return stars, reviews_count

# ─────────────────── GESTION DES BLOCAGES (CAPTCHA & COOKIES) ─────────────────── #

async def handle_cookie_consent(page):
    accept_button = page.get_by_role("button", name=re.compile("accept|accepter", re.IGNORECASE)).or_(
                    page.locator("input[name='accept']"))
    try:
        await accept_button.first.wait_for(state="visible", timeout=7_000)
        logger.info(f"Bannière de cookies détectée pour {page.url}. Clic sur 'Accepter'.")
        await accept_button.first.click(force=True)
        await accept_button.first.wait_for(state="hidden", timeout=5_000)
        logger.info(f"Bannière de cookies fermée avec succès.")
        await page.wait_for_timeout(random.uniform(500, 1000))
        return True
    except PlaywrightTimeoutError:
        return False
    except Exception as e:
        logger.warning(f"Erreur imprévue lors de la gestion des cookies : {e}")
        return False

def is_page_blocked(html, page_title):
    if CAPTCHA_RE.search(page_title):
        return True
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("form", attrs={"action": re.compile(r"validateCaptcha", re.IGNORECASE)}):
        return True
    if soup.find("img", src=re.compile(r"/captcha/", re.IGNORECASE)):
        return True
    return False

async def handle_blocking_page(page):
    continue_button = page.get_by_role("button", name=re.compile(r"continue|proceed|continuer", re.IGNORECASE))
    if await continue_button.is_visible():
        logger.info("Page de vérification simple détectée. Clic sur le bouton de continuation.")
        try:
            await continue_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            return True
        except Exception as e:
            logger.warning(f"Le clic sur le bouton de continuation a échoué : {e}")
            return False

    captcha_img = page.locator("img[src*='/captcha/']")
    if not await captcha_img.is_visible():
        logger.info("Aucun blocage de type CAPTCHA image détecté.")
        return False

    logger.info("CAPTCHA image détecté. Tentative de résolution par OCR.")
    img_src = await captcha_img.get_attribute("src")
    try:
        solution = AmazonCaptcha.fromlink(img_src).solve()
        logger.info(f"[CAPTCHA] Solution OCR : '{solution}'")
    except Exception as e:
        logger.error(f"[CAPTCHA] L'OCR a échoué : {e}")
        return False

    await page.locator("input[type='text'][name*='captcha']").fill(solution)
    await page.get_by_role("button", name=re.compile("submit|continue|try again", re.IGNORECASE)).click()
    
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20_000)
        final_html = await page.content()
        final_title = await page.title()
        if not is_page_blocked(final_html, final_title):
            logger.info("CAPTCHA résolu avec succès.")
            return True
        else:
            logger.warning("La résolution du CAPTCHA a échoué, la page est toujours bloquée.")
            return False
    except PlaywrightTimeoutError:
        logger.warning("Timeout après la soumission du CAPTCHA. Le déblocage a probablement échoué.")
        return False

# ─────────────────────────── SCRAPER CORE ──────────────────────────── #

async def scrape_one(browser,url,sem):
    await asyncio.sleep(random.uniform(*WAIT_BETWEEN_REQUESTS))
    for attempt in range(1, MAX_RETRIES + 1):
        context = None
        try:
            async with sem:
                context = await browser.new_context(
                    user_agent=random.choice(UA_POOL),
                    locale="fr-FR",
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()
                if stealth_async: await stealth_async(page)
                
                logger.info(f"Navigating to {url} (Attempt {attempt}/{MAX_RETRIES})")
                resp: Response | None = await page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")

                if resp and resp.status >= 400:
                    logger.warning(f"URL {url} a retourné le statut HTTP {resp.status}.")
                    await context.close()
                    return {k: "Page n'existe pas" for k in ("availability", "average_stars", "total_reviews")}
                if NOT_FOUND_RE.search(await page.title()):
                    logger.warning(f"URL {url} est une page d'erreur (titre).")
                    await context.close()
                    return {k: "Page n'existe pas" for k in ("availability", "average_stars", "total_reviews")}

                await handle_cookie_consent(page)
                
                for _ in range(2):
                    html = await page.content()
                    title = await page.title()
                    if is_page_blocked(html, title):
                        logger.warning(f"Page bloquée détectée pour {url}. Tentative de résolution...")
                        if not await handle_blocking_page(page):
                            raise RuntimeError(f"Échec de la résolution du blocage pour {url}")
                        await page.wait_for_timeout(random.uniform(1500, 2500))
                    else:
                        break
                else:
                     raise RuntimeError(f"Blocage persistant après plusieurs tentatives pour {url}")              
                final_html = await page.content()
                soup = BeautifulSoup(final_html, "html.parser")
                
                avail = extract_availability(soup)
                stars, reviews = extract_reviews_info(soup)
                
                logger.info(f"Succès pour {url}: Stars={stars}, Reviews={reviews}, Availability='{avail}'")
                await context.close()
                return {"availability": avail, "average_stars": stars, "total_reviews": reviews}

        except (PlaywrightError, RuntimeError, Exception) as e:
            logger.error(f"Échec de la tentative {attempt}/{MAX_RETRIES} pour {url}: {type(e).__name__} - {e}")
            if context: await context.close()
            if attempt < MAX_RETRIES:
                await asyncio.sleep(random.uniform(5, 10))
            else:
                logger.error(f"Toutes les tentatives ont échoué pour {url}.")
                return {k: "Error" for k in ("availability", "average_stars", "total_reviews")}
    return {k: "Error" for k in ("availability", "average_stars", "total_reviews")}

# ─────────────────────────── MAIN ORCHESTRATOR ──────────────────────────── #

async def process_chunk(df_chunk,browser):
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [scrape_one(browser, url, sem) for url in df_chunk["Details Page Link"]]
    results = await asyncio.gather(*tasks)
    res = df_chunk.copy()
    res[["availability", "average_stars", "total_reviews"]] = pd.DataFrame(results, index=res.index)
    return res

def chunker(df,size):
    return [df.iloc[i:i + size].reset_index(drop=True) for i in range(0, len(df), size)]

async def main_async(path):
    try:
        df = pd.read_excel(path) if path.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(path)
    except FileNotFoundError:
        logger.error(f"Le fichier d'entrée '{path}' n'a pas été trouvé.")
        sys.exit(1)
        
    if "Details Page Link" not in df.columns:
        logger.error("La colonne 'Details Page Link' est manquante dans le fichier d'entrée.")
        sys.exit(1)

    if SAMPLE_SIZE > 0:
        df = df.sample(n=min(SAMPLE_SIZE, len(df)), random_state=42).reset_index(drop=True)
        logger.info(f"Mode échantillon actif → Traitement de {len(df)} lignes.")

    chunks = chunker(df, CHUNK_SIZE)
    logger.info(f"{len(df)} lignes divisées en {len(chunks)} chunk(s) de {CHUNK_SIZE} max.")
    
    all_results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for idx, ch in enumerate(chunks, 1):
            logger.info(f"───── Traitement du Chunk {idx}/{len(chunks)} ─────")
            processed_chunk = await process_chunk(ch, browser)
            all_results.append(processed_chunk)
            if idx < len(chunks):
                pause = random.uniform(*WAIT_BETWEEN_CHUNKS)
                logger.info(f"Pause de {pause:.1f}s avant le prochain chunk...")
                await asyncio.sleep(pause)
        await browser.close()

    if all_results:
        full_df = pd.concat(all_results, ignore_index=True)
        full_df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        logger.info(f"Scraping terminé ! Résultats sauvegardés dans → {OUTPUT_FILE} ({len(full_df)} lignes)")
    else:
        logger.warning("Aucun résultat n'a été produit.")


if __name__ == "__main__":
    default_path = ""
    input_file_path = Path(os.getenv("INPUT_FILE", default_path))
    try:
        asyncio.run(main_async(input_file_path))
    except KeyboardInterrupt:
        logger.warning("Script interrompu par l'utilisateur.")
    except Exception as e:
        logger.critical(f"Une erreur fatale a arrêté le script : {e}", exc_info=True)
