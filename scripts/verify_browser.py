import asyncio
from playwright.async_api import async_playwright

async def verify():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        
        errors = []
        page.on("pageerror", lambda exc: errors.append(f"PageError: {exc}"))
        page.on("console", lambda msg: errors.append(f"ConsoleError: {msg.text}") if msg.type == "error" else None)

        print("1. Navigating to Second Opinion app...")
        await page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        assert await page.title() == "Second Opinion | AI Verification & Auditing Platform"

        # Check Demo Presets 0..3
        print("2. Testing Demo 1 (Preset 0)...")
        source_val = await page.input_value("#sourceInput")
        assert "Curiosity rover" in source_val

        print("3. Testing Demo 4 (Sample Audit Loading)...")
        await page.click(".sample-demo-btn")
        await page.wait_for_selector("#resultsSection", state="visible")
        
        # Verify Scoreboard & Gauges
        score_text = await page.inner_text("#scoreNumber")
        print(f"   Canonical Sample Reliability Score: {score_text}")
        assert score_text != "--"

        # Verify Stale Banner triggers on input edit
        print("4. Testing Stale Results detection...")
        stale_banner = page.locator("#staleResultsBanner")
        assert not await stale_banner.is_visible()
        await page.fill("#sourceInput", source_val + " Edited additional statement.")
        assert await stale_banner.is_visible()
        print("   Stale Results Banner appeared upon textarea edit!")

        # Re-run or reload Demo 4 to restore clean audit state
        print("5. Reloading Demo 4 (Sample Audit)...")
        await page.click(".sample-demo-btn")
        await page.wait_for_timeout(500)
        assert not await stale_banner.is_visible()

        # Test Claim selection, Inspector details, Snippet Windowing
        print("6. Verifying Claims & Snippet Windowing...")
        marks = page.locator(".claim-mark")
        count = await marks.count()
        print(f"   Found {count} interactive claim spans in answer text.")
        assert count > 0

        # Click claim 1
        await marks.nth(0).click()
        badge_text = await page.inner_text("#activeClaimBadge")
        print(f"   Active claim badge: {badge_text}")
        assert "Claim 1 of" in badge_text

        # Test Prev/Next Navigation
        print("7. Testing Keyboard / Button Navigation...")
        await page.click("#btnNextClaim")
        badge_text_2 = await page.inner_text("#activeClaimBadge")
        print(f"   Navigated to next claim: {badge_text_2}")
        assert "Claim 2 of" in badge_text_2

        # Test Filters
        print("8. Testing Filter views and counts...")
        # Reset active claim to claim 1 (which is contradicted in canonical sample)
        await marks.nth(0).click()
        await page.click("button[data-filter='contradicted']")
        # Assert non-matching claim (claim 2, which is not active) has opacity 0.45
        mark_opacity = await page.evaluate("() => { const marks = document.querySelectorAll('.claim-mark'); for (const m of marks) { if (m.style.opacity === '0.45') return '0.45'; } return '1'; }")
        print(f"   Filtered non-matching claim opacity: {mark_opacity}")
        assert mark_opacity == "0.45"

        # Reset filter
        await page.click("button[data-filter='all']")

        # Test Window-level drag/drop safety
        print("9. Testing Window Drag and Drop navigation prevention...")
        prevented = await page.evaluate("""() => {
            const ev = new Event('drop', { cancelable: true });
            return !window.dispatchEvent(ev);
        }""")
        print(f"   Window drop default prevented: {prevented}")
        assert prevented is True

        # Test Mobile Viewport
        print("10. Testing Mobile Viewport (375x667)...")
        await page.set_viewport_size({"width": 375, "height": 667})
        await marks.nth(0).click()
        jump_bar = page.locator("#mobileClaimJumpBar")
        assert await jump_bar.is_visible()
        print("   Mobile Jump Bar is visible on narrow screen!")

        # Click jump action to scroll to inspector
        await page.click(".btn-jump-action")
        await page.wait_for_timeout(300)

        # Print layout check
        print("11. Checking Print media styles...")
        await page.emulate_media(media="print")
        # In print, header should be hidden
        header_display = await page.evaluate("() => getComputedStyle(document.querySelector('.app-header')).display")
        assert header_display == "none"
        print(f"   Print stylesheet: app-header display is {header_display}")

        if errors:
            print("Console/Page errors detected:", errors)
            raise AssertionError(f"Detected errors: {errors}")

        print("\n>>> ALL BROWSER AUTOMATION VERIFICATION CHECKS PASSED! <<<")
        await browser.close()

asyncio.run(verify())
