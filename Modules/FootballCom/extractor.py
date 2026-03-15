# extractor.py: Schedule scraper for Football.com.
# Part of LeoBook Modules — Football.com
#
# Functions: extract_league_matches(), validate_match_data()

"""
Extractor Module
Handles extraction of leagues and matches from Football.com schedule pages.
"""

import asyncio
from typing import List, Dict

from playwright.async_api import Page

from Core.Intelligence.selector_manager import SelectorManager

from Core.Utils.constants import WAIT_FOR_LOAD_STATE_TIMEOUT
from .navigator import hide_overlays
from Core.Intelligence.aigo_suite import AIGOSuite


async def _activate_and_wait_for_matches(
    page: Page,
    expected_count: int = 0,
) -> bool:
    """
    Triggers lazy-load hydration on football.com tournament pages
    by scrolling before waiting for match card selectors.

    Returns True if match cards found, False if page is genuinely empty or has no upcoming games.
    """
    # Phase 0: Check for "No upcoming games" message early
    NO_DATA_SELECTORS = [
        ".match-card-error-message",
        ".flex-column.no-data",
        ".match-cards-wrapper-adaptor:has-text('no upcoming games')",
    ]
    
    # Phase 1: Deep Hydration (Wait for Tabs & Initial Content)
    try:
        # Sometimes tabs are inside a skeleton or lazy-loaded
        for i in range(3):
            # Early exit if "No games" message appears
            for sel in NO_DATA_SELECTORS:
                if await page.locator(sel).count() > 0:
                    print(f"    [Extractor] Info: League page indicates no upcoming matches.")
                    return False

            tab_locators = page.locator("li.m-snap-nav-item")
            count = await tab_locators.count()
            if count > 0:
                # Try to switch to 'All' or 'Results' if we see 'Upcoming'
                for j in range(count):
                    tab = tab_locators.nth(j)
                    text = (await tab.inner_text()).lower()
                    if any(x in text for x in ["all", "result", "finish"]):
                        print(f"    [Extractor] Switching to '{text.strip()}' tab...")
                        await tab.click(force=True)
                        await asyncio.sleep(2.0)
                        break
                break
            else:
                await page.evaluate("window.scrollBy(0, 200)")
                await asyncio.sleep(0.8)
    except Exception:
        pass

    # Dynamic wait — fires after DOM is active (Phase 1 complete),
    # before scroll begins (Phase 2). At this point domcontentloaded
    # has fired AND tab structure is confirmed present. We now wait
    # for the card renderer to populate before scrolling.
    # Formula: base 1.0s + 0.25s per expected fixture, cap 5.0s
    # 2 fixtures→1.5s | 6→2.5s | 10→3.5s | 16+→5.0s
    # FIX: when expected_count is 0 (unknown), use a minimal 1.0s base wait
    #      instead of the full formula, to avoid 231× 1.0s sleeps for empty leagues.
    _pre_scroll_wait = min(1.0 + (expected_count * 0.25), 5.0) if expected_count > 0 else 1.0
    await asyncio.sleep(_pre_scroll_wait)

    # Phase 2: Incremental scroll to trigger match card hydration
    try:
        scroll_positions = [400, 800, 1500]
        for pos in scroll_positions:
            await page.evaluate(f"window.scrollTo(0, {pos})")
            await asyncio.sleep(1)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)
    except Exception:
        pass

    # Phase 3: Now wait for cards to appear
    REAL_CARD_SELECTOR = "section.match-card:not(.skeleton), div.match-card:not(.skeleton), [class*='match-card']:not([class*='skeleton'])"
    
    # FIX: reduce per-attempt timeout from 5000ms to 2500ms for empty-league fast exit.
    # 3 attempts × 2.5s = 7.5s max vs old 3 × 5s = 15s, for genuinely empty leagues.
    CARD_WAIT_TIMEOUT = 2500

    for attempt in range(3):
        # Final check for "No games" before timing out
        for sel in NO_DATA_SELECTORS:
            if await page.locator(sel).count() > 0:
                return False

        try:
            # Phase 1: confirm DOM is alive (any card present)
            await page.wait_for_selector(
                REAL_CARD_SELECTOR, state="visible", timeout=CARD_WAIT_TIMEOUT
            )

            # Phase 2: poll until card count reaches expected_count
            # or timeout (scaled by fixture count)
            POLL_INTERVAL = 0.4
            
            # SCALED HYDRATION: Give larger leagues more time to render.
            # Base 8.0s + 0.5s per fixture, capped at 25s.
            POLL_TIMEOUT = max(8.0, min(25.0, expected_count * 0.5)) if expected_count > 0 else 8.0
            
            _elapsed = 0.0
            _found = 0

            while _elapsed < POLL_TIMEOUT:
                _found = await page.evaluate(f"""() => {{
                    const cards = document.querySelectorAll(
                        "section.match-card:not(.skeleton), "
                        + "div.match-card:not(.skeleton), "
                        + "[class*='match-card']:not([class*='skeleton'])"
                    );
                    let count = 0;
                    for (const c of cards) {{
                        if (c.innerText && c.innerText.trim().length > 20)
                            count++;
                    }}
                    return count;
                }}""")

                if expected_count == 0 or _found >= expected_count:
                    break   # All expected cards are hydrated (or no expectation set)

                await asyncio.sleep(POLL_INTERVAL)
                _elapsed += POLL_INTERVAL

            if expected_count > 0 and _found < expected_count:
                # Recovery scroll: one extra deep pass if yield < 60%
                if _found < expected_count * 0.6:
                    try:
                        for pos in [2000, 3500, 0]:
                            await page.evaluate(f"window.scrollTo(0, {pos})")
                            await asyncio.sleep(0.8)
                        # Re-poll for up to 4 more seconds
                        _extra_elapsed = 0.0
                        while _extra_elapsed < 4.0:
                            _found = await page.evaluate("""() => {
                                const cards = document.querySelectorAll(
                                    "section.match-card:not(.skeleton), "
                                    + "div.match-card:not(.skeleton), "
                                    + "[class*='match-card']:not([class*='skeleton'])"
                                );
                                let count = 0;
                                for (const c of cards) {
                                    if (c.innerText && c.innerText.trim().length > 20) count++;
                                }
                                return count;
                            }""")
                            if _found >= expected_count:
                                break
                            await asyncio.sleep(0.4)
                            _extra_elapsed += 0.4
                    except Exception:
                        pass

                print(
                    f"    [Extractor] Partial hydration: "
                    f"{_found}/{expected_count} cards after "
                    f"{POLL_TIMEOUT:.1f}s — proceeding with what's available."
                )
            else:
                print(
                    f"    [Extractor] {_found}/{expected_count if expected_count else '?'} cards OK "
                    f"({POLL_TIMEOUT:.1f}s budget)."
                )
            return True

        except Exception:
            await page.evaluate("window.scrollBy(0, 600)")
            await asyncio.sleep(1.0)

    return False


async def dismiss_overlays(page: Page) -> int:
    """Attempts to dismiss common overlays on football.com."""
    dismissed = 0
    OVERLAY_SELECTORS = [
        "button[id*='accept']", "button[class*='accept']", "button[class*='cookie']",
        ".overlay-close", ".popup-close", "button[class*='close']"
    ]
    for selector in OVERLAY_SELECTORS:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                await el.click(timeout=1000)
                dismissed += 1
                await asyncio.sleep(0.2)
        except Exception: pass
    return dismissed


@AIGOSuite.aigo_retry(max_retries=2, delay=2.0, context_key="fb_schedule_page", element_key="league_section")
async def extract_league_matches(page: Page, target_date: str = None, target_league_name: str = None, fb_url: str = None, expected_count: int = 0) -> List[Dict]:
    """Iterates leagues and extracts matches with AIGO protection and hydration support."""
    if fb_url:
        print(f"    [Extractor] Navigating to {fb_url}...")
        await page.goto(fb_url, wait_until='domcontentloaded', timeout=30000)
    
    current_url = page.url
    print(f"  [Harvest] Sequence for {target_league_name or 'league'} -> {current_url}")

    is_tournament_page = "sr:tournament:" in current_url or "/sport/football/sr:category:" in current_url

    # Selectors
    league_section_sel = SelectorManager.get_selector_strict("fb_schedule_page", "league_section")
    match_card_sel = SelectorManager.get_selector_strict("fb_schedule_page", "match_rows")
    match_url_sel = SelectorManager.get_selector_strict("fb_schedule_page", "match_url")
    league_title_sel = SelectorManager.get_selector_strict("fb_schedule_page", "league_title_link")
    home_team_sel = SelectorManager.get_selector_strict("fb_schedule_page", "match_row_home_team_name")
    away_team_sel = SelectorManager.get_selector_strict("fb_schedule_page", "match_row_away_team_name")
    time_sel = SelectorManager.get_selector_strict("fb_schedule_page", "match_row_time")
    collapsed_icon_sel = SelectorManager.get_selector_strict("fb_schedule_page", "league_expand_icon_collapsed")

    all_matches = []
    
    if is_tournament_page:
        print(f"    [Mode] Direct Tournament Page")
        await dismiss_overlays(page)
        content_ready = await _activate_and_wait_for_matches(
            page, expected_count=expected_count
        )

        if not content_ready:
            return []

        # Use flexible common selectors for cards
        MATCH_CARD_SELECTORS = ["section.match-card", "div.match-card", "[class*='match-card']", "[data-match-id]"]
        discovered_selector = None
        for sel in MATCH_CARD_SELECTORS:
            if await page.locator(sel).count() > 0:
                discovered_selector = sel
                break
        
        if discovered_selector:
            all_matches = await _extract_matches_from_container(
                page, discovered_selector, home_team_sel, away_team_sel,
                time_sel, match_url_sel, target_league_name or "Tournament Matches", target_date
            )
            
    else:
        print(f"    [Mode] Global Schedule Page")
        await dismiss_overlays(page)
        content_ready = await _activate_and_wait_for_matches(
            page, expected_count=expected_count
        )
        
        if not content_ready:
            return []

        league_headers = await page.locator(league_section_sel).all()
        if league_headers:
            for i, header_locator in enumerate(league_headers):
                league_element = header_locator.locator(league_title_sel).first
                league_text = (await league_element.inner_text()).strip().replace('\n', ' - ') if await league_element.count() > 0 else f"Unknown {i+1}"

                if league_text.startswith("Simulated Reality"): continue
                if target_league_name and target_league_name.lower() not in league_text.lower(): continue

                if await header_locator.locator(collapsed_icon_sel).count() > 0:
                    await header_locator.click(force=True)
                    await asyncio.sleep(1.0)

                matches_container = await header_locator.evaluate_handle('(el) => el.nextElementSibling')
                if matches_container:
                    matches_in_section = await _extract_matches_from_container(
                        matches_container, match_card_sel, home_team_sel, away_team_sel,
                        time_sel, match_url_sel, league_text, target_date
                    )
                    if matches_in_section: all_matches.extend(matches_in_section)
        
        if not all_matches:
            # Fallback direct scan
            all_matches = await _extract_matches_from_container(
                page, match_card_sel, home_team_sel, away_team_sel,
                time_sel, match_url_sel, target_league_name or "Unknown League", target_date
            )

    print(f"  [Harvest] Total: {len(all_matches)}")
    return all_matches


async def _extract_matches_from_container(container, match_card_sel, home_team_sel, away_team_sel, time_sel, match_url_sel, league_text, target_date):
    """Internal helper to JS-scrape matches from a container.

    FIX: The original evaluate() call used `document.querySelectorAll` unconditionally,
    meaning the JS function always queried the full page document rather than the
    scoped container element. In the Global Schedule path, this caused matches from
    other league sections to bleed into every section's results.

    The fix passes a scoped root reference into the JS:
    - For a Playwright Page object: root = document (correct, page-wide query is intentional).
    - For a Playwright JSHandle (nextElementSibling result): root = the handle element itself,
      so querySelectorAll is scoped to that section only.
    """
    if not hasattr(container, 'evaluate'):
        return []

    # Determine if container is a Page (use document) or an ElementHandle/JSHandle (use element).
    # Page objects have a .url attribute; ElementHandles do not.
    is_page = hasattr(container, 'url')

    if is_page:
        # Tournament / full-page path: query the entire document.
        return await container.evaluate(r"""(args) => {
            const { selectors, leagueText, targetDate } = args;
            const results = [];
            const cards = document.querySelectorAll(selectors.match_card_sel);
            cards.forEach(card => {
                const homeEl = card.querySelector(selectors.home_team_sel);
                const awayEl = card.querySelector(selectors.away_team_sel);
                const timeEl = card.querySelector(selectors.time_sel);
                const linkEl = card.querySelector(selectors.match_url_sel) || card.closest('a');
                if (homeEl && awayEl) {
                    const dateEl = card.querySelector(
                        '[data-date], [class*="match-date"], '
                        + '[class*="event-date"], [class*="matchdate"], '
                        + '[class*="date-label"]'
                    );
                    let cardDate = dateEl
                        ? (dateEl.dataset.date || dateEl.innerText.trim())
                        : targetDate;
                    if (cardDate && !/^\d{4}-\d{2}-\d{2}$/.test(cardDate)) {
                        cardDate = targetDate;
                    }
                    results.push({
                        home: homeEl.innerText.trim(),
                        away: awayEl.innerText.trim(),
                        time: timeEl ? timeEl.innerText.trim() : "N/A",
                        league: leagueText,
                        url: linkEl ? linkEl.href : "",
                        date: cardDate
                    });
                }
            });
            return results;
        }""", {
            "selectors": {
                "match_card_sel": match_card_sel, "match_url_sel": match_url_sel,
                "home_team_sel": home_team_sel, "away_team_sel": away_team_sel, "time_sel": time_sel
            },
            "leagueText": league_text,
            "targetDate": target_date
        })
    else:
        # Global schedule path: container is a JSHandle for a specific league section.
        # FIX: evaluate() on a JSHandle passes the element as the first JS argument.
        # Use `element.querySelectorAll` to scope to this section only, preventing
        # cross-section bleed.
        return await container.evaluate(r"""(element, args) => {
            const { selectors, leagueText, targetDate } = args;
            const results = [];
            const cards = element.querySelectorAll(selectors.match_card_sel);
            cards.forEach(card => {
                const homeEl = card.querySelector(selectors.home_team_sel);
                const awayEl = card.querySelector(selectors.away_team_sel);
                const timeEl = card.querySelector(selectors.time_sel);
                const linkEl = card.querySelector(selectors.match_url_sel) || card.closest('a');
                if (homeEl && awayEl) {
                    const dateEl = card.querySelector(
                        '[data-date], [class*="match-date"], '
                        + '[class*="event-date"], [class*="matchdate"], '
                        + '[class*="date-label"]'
                    );
                    let cardDate = dateEl
                        ? (dateEl.dataset.date || dateEl.innerText.trim())
                        : targetDate;
                    if (cardDate && !/^\d{4}-\d{2}-\d{2}$/.test(cardDate)) {
                        cardDate = targetDate;
                    }
                    results.push({
                        home: homeEl.innerText.trim(),
                        away: awayEl.innerText.trim(),
                        time: timeEl ? timeEl.innerText.trim() : "N/A",
                        league: leagueText,
                        url: linkEl ? linkEl.href : "",
                        date: cardDate
                    });
                }
            });
            return results;
        }""", {
            "selectors": {
                "match_card_sel": match_card_sel, "match_url_sel": match_url_sel,
                "home_team_sel": home_team_sel, "away_team_sel": away_team_sel, "time_sel": time_sel
            },
            "leagueText": league_text,
            "targetDate": target_date
        })


async def validate_match_data(matches: List[Dict]) -> List[Dict]:
    """Validate and clean extracted match data."""
    valid_matches = []
    for match in matches:
        if all(k in match for k in ['home', 'away', 'url', 'league']) and match['home'] and match['away'] and match['url']:
            valid_matches.append(match)
    print(f"  [Validation] {len(valid_matches)}/{len(matches)} valid.")
    return valid_matches
