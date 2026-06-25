"""WhatsApp Web auto-send via Selenium — runs on the LOCAL machine (Windows/Mac).

Auto-send drives a real, logged-in WhatsApp Web session in Chrome on the same machine
the app runs on. A persistent Chrome profile keeps the login (scan the QR once). Unlike
click-to-send (wa.me) links — which can ONLY carry text — auto-send can attach media
AND include a caption.

Caveats (be honest with the user):
- Needs Chrome installed and `pip install selenium`. Selenium 4.6+ auto-manages the driver.
- One-time QR login via the persistent profile.
- WhatsApp Web's page structure changes occasionally; selectors may need a tweak.
- Sending to many numbers fast risks account flags — keep a delay between sends.
"""

import os
import time
import urllib.parse

import paths


def _profile_dir():
    d = os.path.join(paths.common_dir(), "wa_profile")
    os.makedirs(d, exist_ok=True)
    return d


def selenium_available():
    try:
        import selenium  # noqa
        return True
    except Exception:
        return False


def likely_server():
    """Are we on a hosted server (no local browser/WhatsApp) rather than the user's machine?"""
    cwd = os.getcwd()
    return (cwd.startswith("/mount/src") or cwd.startswith("/app")
            or os.environ.get("PMD_CLOUD") == "1")


def _normalize(number, cc="91"):
    import re
    d = re.sub(r"\D", "", str(number or ""))
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10:
        d = cc + d
    return d


# CSS selectors (kept in one place so they're easy to tweak when WhatsApp Web changes)
_MSG_BOX = ('footer div[contenteditable="true"], '
            'div[contenteditable="true"][data-tab="10"], '
            'div[contenteditable="true"][role="textbox"]')
_CAPTION_BOX = ('div[aria-label="Add a caption"]',
                'div[aria-label="Add a caption…"]',
                'div[aria-label="Type a message"]',
                'div[contenteditable="true"][data-tab="10"]',
                'div[contenteditable="true"][role="textbox"]')
_ATTACH_BTN = ('span[data-icon="plus"]', 'span[data-icon="plus-rounded"]', 'span[data-icon="clip"]',
               'span[data-icon="attach-menu-plus"]', 'div[title="Attach"]')
_SEND_BTN = ('span[data-icon="send"]', 'span[data-icon="wds-ic-send-filled"]',
             'button[aria-label="Send"]', 'span[data-testid="send"]',
             '[aria-label="Send"]', 'button[data-tab="11"]')


class WhatsAppSender:
    """Context manager that opens one Chrome/WhatsApp-Web session for a batch."""

    def __init__(self, headless=False):
        self.headless = headless
        self.driver = None

    def __enter__(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_argument(f"--user-data-dir={_profile_dir()}")
        opts.add_argument("--profile-directory=Default")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_experimental_option("excludeSwitches", ["enable-logging"])
        if self.headless:
            opts.add_argument("--headless=new")
        self.driver = webdriver.Chrome(options=opts)
        self.driver.set_page_load_timeout(60)
        return self

    def __exit__(self, *a):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass

    def is_logged_in(self, wait=25):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        self.driver.get("https://web.whatsapp.com")
        try:
            WebDriverWait(self.driver, wait).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'div[role="grid"], div[aria-label="Chat list"]')))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ helpers

    def _first_visible(self, selectors):
        """Return the first displayed element matching any selector, else None."""
        from selenium.webdriver.common.by import By
        for s in selectors:
            try:
                for e in self.driver.find_elements(By.CSS_SELECTOR, s):
                    if e.is_displayed():
                        return e
            except Exception:
                continue
        return None

    def _wait_visible(self, selectors, timeout):
        """Poll until a displayed element matches any selector, else None."""
        end = time.time() + timeout
        while time.time() < end:
            el = self._first_visible(selectors)
            if el is not None:
                return el
            time.sleep(0.5)
        return None

    def _type_multiline(self, el, text):
        """Enter text into a WhatsApp contenteditable. Primary: clipboard paste (commits
        emojis + newlines to React reliably). Fallbacks: JS insertText, then send_keys with
        non-BMP (emoji) chars stripped (ChromeDriver's send_keys crashes on emojis)."""
        text = text or ""
        # primary: clipboard paste
        if self._paste_text(el, text):
            try:
                if (el.text or "").strip():
                    return
            except Exception:
                return
        # fallback 1: JS insertText (emoji-safe), line by line to preserve line breaks
        try:
            el.click()
        except Exception:
            pass
        # primary: JS insertText (emoji-safe), line by line to preserve line breaks
        try:
            self.driver.execute_script("arguments[0].focus();", el)
            self.driver.execute_script(
                "document.execCommand('selectAll', false, null);"
                "document.execCommand('delete', false, null);")
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if i:
                    self.driver.execute_script(
                        "document.execCommand('insertText', false, '\\n');")
                if line:
                    self.driver.execute_script(
                        "document.execCommand('insertText', false, arguments[0]);", line)
            # confirm something landed
            try:
                cur = (el.text or "").strip()
            except Exception:
                cur = "x"
            if cur:
                return
        except Exception:
            pass
        # fallback: send_keys with non-BMP (emoji) chars stripped so it can't crash
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        safe = "".join(ch for ch in text if ord(ch) <= 0xFFFF)
        try:
            self.driver.execute_script("arguments[0].focus();", el)
        except Exception:
            pass
        for i, line in enumerate(safe.split("\n")):
            if i:
                ActionChains(self.driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER)\
                    .key_up(Keys.SHIFT).perform()
            if line:
                try:
                    el.send_keys(line)
                except Exception:
                    pass

    def _click_send(self):
        from selenium.webdriver.common.by import By
        for s in _SEND_BTN:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, s)
                if btn.is_displayed():
                    btn.click()
                    return True
            except Exception:
                continue
        return False

    def _find_caption_box(self, exclude=None):
        """The media-preview caption box: the first visible contenteditable that is NOT the
        original chat box (captured before attaching). Returns the element or None."""
        from selenium.webdriver.common.by import By
        boxes = [b for b in self.driver.find_elements(
                 By.CSS_SELECTOR, 'div[contenteditable="true"]') if b.is_displayed()]
        for b in boxes:
            try:
                if exclude is not None and b == exclude:
                    continue
            except Exception:
                pass
            return b
        return boxes[-1] if boxes else None

    def _paste_text(self, el, text):
        """Put text on the clipboard and paste into el. This reliably commits emojis AND
        newlines into WhatsApp's editor (React state updates on paste), unlike send_keys
        which crashes on non-BMP characters like emojis."""
        import sys
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        try:
            self.driver.execute_script(
                "const t=document.createElement('textarea');"
                "t.value=arguments[0];"
                "t.style.position='fixed';t.style.top='0';t.style.opacity='0';"
                "document.body.appendChild(t);t.focus();t.select();"
                "document.execCommand('copy');document.body.removeChild(t);", text)
            el.click()
            time.sleep(0.2)
            mod = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
            ActionChains(self.driver).key_down(mod).send_keys('v').key_up(mod).perform()
            time.sleep(0.6)
            return True
        except Exception:
            return False

    def _attach_file(self, path, wait):
        """Reveal the file input and push the media into it. WhatsApp Web has several
        input[type=file] elements (photos/videos vs document) — we pick the one that
        accepts images/videos. Returns True if a preview opened."""
        from selenium.webdriver.common.by import By
        # best-effort: click the attach (+) button to reveal the inputs
        for s in _ATTACH_BTN:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, s)
                if el.is_displayed():
                    el.click(); time.sleep(1.0); break
            except Exception:
                continue
        # find the media file input
        inputs = self.driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        target = None
        for inp in inputs:
            acc = (inp.get_attribute("accept") or "").lower()
            if any(k in acc for k in ("image", "video", "mp4", "mov")):
                target = inp; break
        if target is None and inputs:
            target = inputs[-1]   # media input is usually the last one
        if target is None:
            return False
        try:
            target.send_keys(os.path.abspath(path))
        except Exception:
            return False
        # confirm the preview opened: a send button OR a caption box becomes visible
        end = time.time() + wait
        while time.time() < end:
            if self._first_visible(_SEND_BTN) is not None \
               or self._first_visible(_CAPTION_BOX) is not None:
                return True
            time.sleep(0.5)
        return False

    # ------------------------------------------------------------------ send

    def send(self, number, message, media_path=None, cc="91", wait=45):
        """Send one message. With media, attaches the file AND types the caption into the
        preview's caption box. Without media, types the text into the chat box. Multiline
        safe. Returns (ok, error)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        d = self.driver
        num = _normalize(number, cc)
        try:
            d.get(f"https://web.whatsapp.com/send?phone={num}")
            box = WebDriverWait(d, wait).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _MSG_BOX)))
            time.sleep(2)

            if media_path:
                if not os.path.exists(media_path):
                    return False, f"Media file not found: {media_path}"
                # attach + confirm the preview opened
                if not self._attach_file(media_path, wait):
                    return False, ("Couldn't attach the media (no preview opened — "
                                   "WhatsApp Web attach UI may have changed).")
                # type the caption into the PREVIEW caption box (NOT the original chat box)
                if message:
                    cap = None
                    end = time.time() + 12
                    while time.time() < end and cap is None:
                        cap = self._find_caption_box(exclude=box)
                        if cap is None:
                            time.sleep(0.5)
                    if cap is not None:
                        self._type_multiline(cap, message)
                        time.sleep(1.0)   # let WhatsApp register the caption
                        # send: click send button, else Enter in the caption box
                        if not self._click_send():
                            try:
                                cap.send_keys(Keys.ENTER)
                            except Exception:
                                pass
                        time.sleep(1.5)
                        # confirm preview closed (media+caption sent)
                        ok = False
                        end2 = time.time() + 12
                        while time.time() < end2:
                            if self._find_caption_box(exclude=box) is None:
                                ok = True; break
                            time.sleep(0.6)
                        time.sleep(1.0)
                        return (True, "") if ok else (False,
                                "Media attached and caption typed, but the send wasn't "
                                "confirmed (WhatsApp Web layout may have changed).")
                # no caption — just send the media
                if not self._click_send():
                    try:
                        (self._find_caption_box(exclude=box) or box).send_keys(Keys.ENTER)
                    except Exception:
                        pass
                time.sleep(2)
                return True, ""
            else:
                # plain text — type into the chat box
                if message:
                    self._type_multiline(box, message)
                    time.sleep(0.6)

            # 4) send (button, else Enter on the active box)
            if not self._click_send():
                try:
                    active = self._first_visible(_CAPTION_BOX if media_path else (_MSG_BOX,)) or box
                    active.send_keys(Keys.ENTER)
                except Exception:
                    pass

            # 5) verify it went (the chat box clears on send for text; for media the
            #    preview closes). Media is treated as sent once the preview is gone.
            ok = False
            end = time.time() + 12
            while time.time() < end:
                try:
                    if media_path:
                        # preview gone? no visible caption box left
                        if self._first_visible(_CAPTION_BOX) is None:
                            ok = True; break
                    else:
                        cur = d.find_element(By.CSS_SELECTOR, _MSG_BOX).text
                        if not (cur or "").strip():
                            ok = True; break
                except Exception:
                    ok = True; break
                time.sleep(0.6)
            time.sleep(1.5)
            if ok:
                return True, ""
            return False, "Send not confirmed (WhatsApp Web layout may have changed)."
        except Exception as e:
            return False, str(e)


def send_bulk(recipients, message, media_path=None, cc="91", delay=6, progress=None):
    """recipients: list of (name, number) OR (name, number, personalised_message).
    If a 3rd element is present it's that recipient's message; else `message`.
    media_path (optional) is attached to EVERY message in the batch.
    Returns list of (name, ok, error). One browser session for the batch."""
    results = []
    with WhatsAppSender(headless=False) as wa:
        if not wa.is_logged_in():
            return [(r[0], False, "WhatsApp Web not logged in — scan the QR first.") for r in recipients]
        for i, rec in enumerate(recipients):
            name, number = rec[0], rec[1]
            msg = rec[2] if len(rec) > 2 else message
            ok, err = wa.send(number, msg, media_path, cc)
            results.append((name, ok, err))
            if progress:
                progress(i + 1, len(recipients), name, ok)
            if i + 1 < len(recipients):
                time.sleep(delay)
        time.sleep(3)   # let the last message flush before the browser closes
    return results


def open_login():
    """Open WhatsApp Web for the one-time QR scan; blocks until logged in or times out."""
    with WhatsAppSender(headless=False) as wa:
        return wa.is_logged_in(wait=120)
