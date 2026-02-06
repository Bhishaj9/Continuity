from playwright.sync_api import sync_playwright, expect
import os
import time

def run():
    os.makedirs("/home/jules/verification", exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Set a large enough viewport to show md:flex items
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        try:
            # 1. Navigate to the app
            print("Navigating to app...")
            page.goto("http://localhost:7860")

            # 2. Check for Auth Modal and Sign In
            # Assuming the modal is visible initially because no token
            print("Waiting for auth modal...")
            page.get_by_text("Authentication Required").wait_for()
            print("Clicking Sign In...")
            page.get_by_role("button", name="Sign In with Google").click()

            # 3. Wait for modal to disappear and main UI to be accessible
            print("Waiting for modal to hide...")
            expect(page.locator("#auth-modal")).to_be_hidden()

            # 4. Check for Credits display
            # It fetches from /billing/balance. Default balance is 0.
            # "Credits: 0"
            print("Waiting for credit balance...")
            credit_balance = page.locator("#credit-balance")
            expect(credit_balance).to_have_text("0")

            # 5. Check for Buy button
            print("Checking Buy button...")
            buy_btn = page.get_by_role("button", name="Buy")
            expect(buy_btn).to_be_visible()

            # 6. Take screenshot
            print("Taking screenshot...")
            page.screenshot(path="/home/jules/verification/billing_ui.png")
            print("Screenshot saved.")

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="/home/jules/verification/error.png")
        finally:
            browser.close()

if __name__ == "__main__":
    run()
