"""Browserbase-backed tool functions for the harness."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, urlparse

from images import browser_image
from models import BrowserFetchInput, BrowserSearchInput
from tensorlake.applications import function

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _resolve_required(value: str, env_name: str, label: str) -> str:
    resolved = value or os.getenv(env_name, "")
    if not resolved:
        raise ValueError(f"Missing required {label}. Set {env_name} or pass it in input.")
    return resolved



def _collect_page_with_browserbase(input: BrowserFetchInput) -> dict[str, Any]:
    from browserbase import Browserbase
    from playwright.sync_api import sync_playwright

    api_key = _resolve_required(
        input.browserbase_api_key,
        "BROWSERBASE_API_KEY",
        "Browserbase API key",
    )
    project_id = _resolve_required(
        input.browserbase_project_id,
        "BROWSERBASE_PROJECT_ID",
        "Browserbase project ID",
    )

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.url)
    if allowed_domain and requested_domain != allowed_domain:
        raise ValueError(
            f"Requested URL domain '{requested_domain}' is outside allowed domain '{allowed_domain}'."
        )

    bb = Browserbase(api_key=api_key)
    session = bb.sessions.create(project_id=project_id)
    connect_url = getattr(session, "connect_url", None) or getattr(session, "connectUrl", None)
    session_id = getattr(session, "id", None)

    if not connect_url:
        raise RuntimeError("Browserbase session did not return a connect URL.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(input.url, wait_until="domcontentloaded", timeout=input.timeout_ms)
            if input.wait_after_load_ms > 0:
                page.wait_for_timeout(input.wait_after_load_ms)

            extracted = page.evaluate(
                """
                ({ maxLinks, maxChars }) => {
                  const toAbsolute = (href) => {
                    try {
                      return new URL(href, window.location.href).toString();
                    } catch {
                      return null;
                    }
                  };

                  const seen = new Set();
                  const links = [];
                  const anchors = Array.from(document.querySelectorAll('a[href]'));

                  for (const anchor of anchors) {
                    const absolute = toAbsolute(anchor.getAttribute('href'));
                    if (!absolute) continue;
                    if (!(absolute.startsWith('http://') || absolute.startsWith('https://'))) continue;
                    if (seen.has(absolute)) continue;
                    seen.add(absolute);
                    links.push(absolute);
                    if (links.length >= maxLinks) break;
                  }

                  const raw = document.body ? document.body.innerText : '';
                  const cleaned = raw
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t]+\\n/g, '\\n')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .trim();

                  return {
                    title: document.title || '',
                    text: cleaned.slice(0, maxChars),
                    links,
                  };
                }
                """,
                {"maxLinks": input.max_links, "maxChars": input.max_chars},
            )

            final_url = page.url
            page.close()
        finally:
            browser.close()

    links = extracted.get("links", [])
    if allowed_domain:
        links = [link for link in links if _extract_domain(link) == allowed_domain]

    return {
        "success": True,
        "requested_url": input.url,
        "url": final_url,
        "title": extracted.get("title", ""),
        "text": extracted.get("text", ""),
        "links": links,
        "session_id": session_id,
        "fetched_at": _now_iso(),
    }


def _search_site_with_browserbase(input: BrowserSearchInput) -> dict[str, Any]:
    from browserbase import Browserbase
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    api_key = _resolve_required(
        input.browserbase_api_key,
        "BROWSERBASE_API_KEY",
        "Browserbase API key",
    )
    project_id = _resolve_required(
        input.browserbase_project_id,
        "BROWSERBASE_PROJECT_ID",
        "Browserbase project ID",
    )

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.start_url)
    if allowed_domain and requested_domain != allowed_domain:
        raise ValueError(
            f"Requested URL domain '{requested_domain}' is outside allowed domain '{allowed_domain}'."
        )

    bb = Browserbase(api_key=api_key)
    session = bb.sessions.create(project_id=project_id)
    connect_url = getattr(session, "connect_url", None) or getattr(session, "connectUrl", None)
    session_id = getattr(session, "id", None)
    if not connect_url:
        raise RuntimeError("Browserbase session did not return a connect URL.")

    search_strategy = "none"
    search_selector = ""

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            parsed = urlparse(input.start_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            encoded_query = quote_plus(input.search_query)

            # 1) Try visible search bar on the start page (hero search on CMS).
            visited_search_urls: list[str] = []
            input_found = False
            last_error = None

            page.goto(input.start_url, wait_until="domcontentloaded", timeout=input.timeout_ms)
            if input.wait_after_load_ms > 0:
                page.wait_for_timeout(input.wait_after_load_ms)

            selectors = [
                "#hero-search-input",
                ".hero-search-block input[name='keys']",
                "#hero-search-block-form input[name='keys']",
                "input[name='keys']",
                "input[id*='keys' i]",
                "input[type='search']",
                "form[role='search'] input",
                "[role='search'] input",
                "input[name*='search' i]",
                "input[id*='search' i]",
                "input[aria-label*='search' i]",
                "input[placeholder*='search' i]",
                "header input[type='text']",
            ]

            submit_selectors = [
                "#hero-search-block-form button[type='submit']",
                ".hero-search-block button[type='submit']",
                "button[id^='edit-submit'][type='submit']",
                "button:has-text('Search')",
                "input[type='submit'][value*='Search' i]",
            ]

            for selector in selectors:
                locator = page.locator(selector)
                try:
                    if locator.count() == 0:
                        continue
                except Exception:
                    continue

                candidate = locator.first
                try:
                    visible = False
                    try:
                        visible = candidate.is_visible(timeout=1200)
                    except Exception:
                        visible = False
                    if not visible:
                        continue

                    candidate.click(timeout=2500)
                    candidate.fill("")
                    candidate.fill(input.search_query, timeout=7000)

                    submitted = False
                    for submit_selector in submit_selectors:
                        submit_locator = page.locator(submit_selector)
                        try:
                            if submit_locator.count() == 0:
                                continue
                            submit_button = submit_locator.first
                            if submit_button.is_visible(timeout=800):
                                submit_button.click(timeout=2500)
                                submitted = True
                                search_selector = f"{selector} + {submit_selector}"
                                break
                        except Exception:
                            continue

                    if not submitted:
                        candidate.press("Enter")
                        search_selector = selector

                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(input.timeout_ms, 30000))
                    except PlaywrightTimeoutError:
                        pass
                    if input.wait_after_submit_ms > 0:
                        page.wait_for_timeout(input.wait_after_submit_ms)

                    quality = page.evaluate(
                        """
                        () => {
                          const bodyText = (document.body?.innerText || '').toLowerCase();
                          const hasPrompt = bodyText.includes('please enter some search terms');
                          const countResultRows = document.querySelectorAll(
                            '.search-item-list .result, .search-item-list .search-results, .gsc-webResult.gsc-result, .views-row'
                          ).length;
                          const countResultLinks = document.querySelectorAll(
                            '.search-item-list .result a[href], .search-item-list .search-results a[href], .gsc-webResult.gsc-result a[href], .views-row a[href]'
                          ).length;
                          return { hasPrompt, countResultRows, countResultLinks };
                        }
                        """
                    )
                    visited_search_urls.append(page.url)
                    if quality.get("countResultRows", 0) > 0 or quality.get("countResultLinks", 0) > 0:
                        search_strategy = "search_input"
                        input_found = True
                        break
                except Exception as exc:
                    last_error = exc
                    continue

            # 2) Deterministic search URL fallback (very reliable for CMS with ?keys=...).
            if not input_found:
                fallback_urls = [
                    f"{origin}/search/cms?keys={encoded_query}",
                    f"{origin}/search?keys={encoded_query}",
                    f"{origin}/search?q={encoded_query}",
                    f"{origin}/search?query={encoded_query}",
                    f"{origin}/site-search?search_api_fulltext={encoded_query}",
                ]

                for fallback_url in fallback_urls:
                    try:
                        page.goto(
                            fallback_url,
                            wait_until="domcontentloaded",
                            timeout=min(input.timeout_ms, 35000),
                        )
                        if input.wait_after_submit_ms > 0:
                            page.wait_for_timeout(input.wait_after_submit_ms)

                        quality = page.evaluate(
                            """
                            () => {
                              const bodyText = (document.body?.innerText || '').toLowerCase();
                              const hasPrompt = bodyText.includes('please enter some search terms');
                              const countResultRows = document.querySelectorAll(
                                '.search-item-list .result, .search-item-list .search-results, .gsc-webResult.gsc-result, .views-row'
                              ).length;
                              const countResultLinks = document.querySelectorAll(
                                '.search-item-list .result a[href], .search-item-list .search-results a[href], .gsc-webResult.gsc-result a[href], .views-row a[href]'
                              ).length;
                              return { hasPrompt, countResultRows, countResultLinks };
                            }
                            """
                        )
                        visited_search_urls.append(page.url)
                        if quality.get("countResultRows", 0) > 0 or quality.get("countResultLinks", 0) > 0 or not quality.get("hasPrompt", False):
                            search_strategy = "direct_search_url"
                            search_selector = fallback_url
                            input_found = True
                            break
                    except Exception as exc:
                        last_error = exc

            if not input_found:
                if last_error:
                    raise RuntimeError(f"Could not execute site search: {last_error}") from last_error
                raise RuntimeError("Could not locate a search input or working search URL.")

            extracted = page.evaluate(
                """
                ({ maxResults, allowedDomain, query }) => {
                  const normalize = (href) => {
                    try {
                      return new URL(href, window.location.href).toString();
                    } catch {
                      return null;
                    }
                  };
                  const domainOf = (href) => {
                    try {
                      return new URL(href).hostname.toLowerCase();
                    } catch {
                      return "";
                    }
                  };
                  const compact = (text) =>
                    (text || "")
                      .replace(/\\u00a0/g, " ")
                      .replace(/\\s+/g, " ")
                      .trim();

                  const queryTerms = (query || "")
                    .toLowerCase()
                    .split(/[^a-z0-9]+/)
                    .filter((t) => t.length >= 3);

                  const looksLikeNav = (url, title) => {
                    const u = (url || "").toLowerCase();
                    const t = (title || "").toLowerCase();
                    if (u.includes('#skip') || u.endsWith('#') || u.includes('javascript:')) return true;
                    if (['about cms', 'newsroom', 'data & research', 'search', 'contact us'].includes(t)) return true;
                    return false;
                  };

                  const scoreResult = (item) => {
                    const hay = `${item.title} ${item.snippet} ${item.url}`.toLowerCase();
                    let score = 0;
                    for (const term of queryTerms) {
                      const hits = hay.split(term).length - 1;
                      score += hits * 3;
                    }
                    if (hay.includes('diabetes')) score += 8;
                    if (hay.includes('coverage')) score += 6;
                    if (hay.includes('medicare-coverage-database')) score += 7;
                    if (hay.includes('policy article') || hay.includes('decision memo')) score += 5;
                    if (looksLikeNav(item.url, item.title)) score -= 12;
                    return score;
                  };

                  const collectFromRows = (rows) => {
                    const list = [];
                    for (const row of rows) {
                      const anchor = row.querySelector('a[href]');
                      if (!anchor) continue;
                      const absolute = normalize(anchor.getAttribute('href'));
                      if (!absolute) continue;
                      if (!(absolute.startsWith('http://') || absolute.startsWith('https://'))) continue;
                      if (allowedDomain && domainOf(absolute) !== allowedDomain) continue;

                      const title = compact(anchor.innerText || anchor.textContent).slice(0, 180);
                      let snippetRaw = compact(row.innerText || row.textContent);
                      if (snippetRaw === title) {
                        const maybe = row.querySelector('.snippet, .description, p, .search-snippet, .gs-snippet');
                        if (maybe) snippetRaw = compact(maybe.innerText || maybe.textContent);
                      }
                      const snippet = snippetRaw.length > 420 ? snippetRaw.slice(0, 420) + "..." : snippetRaw;
                      if (!title && !snippet) continue;
                      list.push({ url: absolute, title, snippet });
                    }
                    return list;
                  };

                  const seen = new Set();
                  const results = [];

                  // Prefer explicit search-result containers.
                  const rowSelectors = [
                    ".search-item-list .result",
                    ".search-item-list .search-results",
                    ".gsc-webResult.gsc-result",
                    ".gsc-result",
                    ".views-row",
                    "main article.search-result",
                    "main li.search-result",
                  ];

                  let candidates = [];
                  for (const sel of rowSelectors) {
                    const rows = Array.from(document.querySelectorAll(sel));
                    if (!rows.length) continue;
                    candidates = candidates.concat(collectFromRows(rows));
                  }

                  // Fallback to anchors in main content if no explicit rows.
                  if (!candidates.length) {
                    const anchors = Array.from(
                      document.querySelectorAll("main a[href], [role='main'] a[href]")
                    );
                    for (const anchor of anchors) {
                      const absolute = normalize(anchor.getAttribute("href"));
                      if (!absolute) continue;
                      if (!(absolute.startsWith("http://") || absolute.startsWith("https://"))) continue;
                      if (allowedDomain && domainOf(absolute) !== allowedDomain) continue;
                      const title = compact(anchor.innerText || anchor.textContent).slice(0, 180);
                      const container = anchor.closest(".result, article, li, section, div");
                      const snippetRaw = container ? compact(container.innerText || container.textContent) : "";
                      const snippet = snippetRaw.length > 360 ? snippetRaw.slice(0, 360) + "..." : snippetRaw;
                      if (!title && !snippet) continue;
                      candidates.push({ url: absolute, title, snippet });
                    }
                  }

                  // Deduplicate + score + sort.
                  const deduped = [];
                  for (const item of candidates) {
                    if (!item.url) continue;
                    if (seen.has(item.url)) continue;
                    seen.add(item.url);
                    deduped.push({ ...item, score: scoreResult(item) });
                  }

                  deduped.sort((a, b) => b.score - a.score);

                  for (const item of deduped) {
                    if (results.length >= maxResults) break;
                    if (looksLikeNav(item.url, item.title) && item.score < 2) continue;
                    results.push({
                      url: item.url,
                      title: item.title,
                      snippet: item.snippet,
                      score: item.score,
                    });
                  }

                  const bodyText = (document.body?.innerText || '').toLowerCase();

                  return {
                    search_url: window.location.href,
                    page_title: document.title || "",
                    extracted_candidates: deduped.length,
                    has_no_terms_prompt: bodyText.includes('please enter some search terms'),
                    results,
                  };
                }
                """,
                {
                    "maxResults": input.max_results,
                    "allowedDomain": allowed_domain or "",
                    "query": input.search_query,
                },
            )
            page.close()
        finally:
            browser.close()

    return {
        "success": True,
        "start_url": input.start_url,
        "search_query": input.search_query,
        "search_url": extracted.get("search_url", input.start_url),
        "page_title": extracted.get("page_title", ""),
        "extracted_candidates": extracted.get("extracted_candidates", 0),
        "has_no_terms_prompt": extracted.get("has_no_terms_prompt", False),
        "results": extracted.get("results", []),
        "search_strategy": search_strategy,
        "search_selector": search_selector,
        "visited_search_urls": visited_search_urls,
        "session_id": session_id,
        "fetched_at": _now_iso(),
    }

@function(image=browser_image, secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"])
def fetch_page(input: BrowserFetchInput) -> dict[str, Any]:
    """Fetch a page with Browserbase and return title/text/links."""
    try:
        return _collect_page_with_browserbase(input)
    except Exception as exc:
        return {
            "success": False,
            "url": input.url,
            "error": str(exc),
        }


@function(image=browser_image, secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"])
def search_site(input: BrowserSearchInput) -> dict[str, Any]:
    """Use website search UI (or fallback search endpoint) to collect relevant result links."""
    try:
        return _search_site_with_browserbase(input)
    except Exception as exc:
        return {
            "success": False,
            "start_url": input.start_url,
            "search_query": input.search_query,
            "error": str(exc),
        }
