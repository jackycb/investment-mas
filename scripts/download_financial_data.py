#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Nov 26 09:40:55 2022

@author: chengbo
"""
import urllib.request
import re
import os
import shutil
import platform
import subprocess
import time
import random
import argparse
import json
import zipfile
from io import StringIO
from pathlib import Path
from typing import Optional
import pandas as pd
import numpy as np
import requests
import difflib
#import Levenshtein
from lxml import etree
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, SessionNotCreatedException
import selenium.webdriver.support.ui as ui
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
#from futu import *

project_root = Path(__file__).resolve().parents[1]
localPath = str(project_root / 'data' / 'finance_report_web') + '/'
DEFAULT_FUTU_PROFILE_DIR = project_root / "data" / "browser_profiles" / "futu"
FUTU_LOGIN_STATE_FILE = DEFAULT_FUTU_PROFILE_DIR / ".login_ready.json"
MANAGED_CHROMEDRIVER_DIR = project_root / "data" / "browser_tools" / "chromedriver"
CFT_LATEST_PATCH_VERSIONS_URL = "https://googlechromelabs.github.io/chrome-for-testing/latest-patch-versions-per-build.json"
CFT_DOWNLOAD_BASE_URL = "https://storage.googleapis.com/chrome-for-testing-public"
LEGACY_ROOT_CACHE_FILE = project_root / "data" / "finance_report_web" / ".legacy_root_xpath_cache.json"
LEGACY_ROOT_DEFAULT_XPATHS = [
    "/html/body/div[2]/div[2]/div[1]/div[1]/section[1]/div[1]/section[1]/section[1]/section[1]",
    "/html/body/div[2]/div/div[1]/section/div/section/section/section",
    "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section",
]
_LEGACY_ROOT_XPATH_CACHE: Optional[dict[str, str]] = None


def _cache_key_from_financial_url(url: str) -> str:
    m = re.search(r"/stock/([^/]+)/financial/([^/?#]+)", url)
    if m:
        return f"{m.group(1)}|{m.group(2)}"
    return url


def _load_legacy_root_xpath_cache() -> dict[str, str]:
    global _LEGACY_ROOT_XPATH_CACHE
    if _LEGACY_ROOT_XPATH_CACHE is not None:
        return _LEGACY_ROOT_XPATH_CACHE
    try:
        if LEGACY_ROOT_CACHE_FILE.exists():
            _LEGACY_ROOT_XPATH_CACHE = json.loads(LEGACY_ROOT_CACHE_FILE.read_text(encoding="utf-8"))
        else:
            _LEGACY_ROOT_XPATH_CACHE = {}
    except Exception:
        _LEGACY_ROOT_XPATH_CACHE = {}
    return _LEGACY_ROOT_XPATH_CACHE


def _save_legacy_root_xpath_cache() -> None:
    if _LEGACY_ROOT_XPATH_CACHE is None:
        return
    try:
        LEGACY_ROOT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LEGACY_ROOT_CACHE_FILE.write_text(
            json.dumps(_LEGACY_ROOT_XPATH_CACHE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _resolve_chromedriver_path() -> str:
    if os.getenv("DISABLE_LOCAL_CHROMEDRIVER", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("local chromedriver disabled by environment")
    env_val = os.getenv("CHROMEDRIVER_PATH", "").strip()
    chrome_version = _resolve_chrome_version()
    chrome_major = _version_major(chrome_version)
    if not chrome_major:
        raise RuntimeError("unable to determine local Chrome version")

    explicit_candidate = _normalize_executable_path(env_val) if env_val else ""
    if explicit_candidate and _binary_major_version(explicit_candidate) == chrome_major:
        return explicit_candidate

    managed_candidate = _find_cached_managed_chromedriver(chrome_major)
    if managed_candidate:
        return managed_candidate

    system_candidates = [
        explicit_candidate,
        "/opt/homebrew/bin/chromedriver",
        "/usr/local/chromedriver/chromedriver",
        "/usr/local/bin/chromedriver",
        "/usr/bin/chromedriver",
        shutil.which("chromedriver") or "",
    ]
    for candidate in system_candidates:
        normalized = _normalize_executable_path(candidate)
        if normalized and _binary_major_version(normalized) == chrome_major:
            return normalized

    managed_download = _ensure_managed_chromedriver(chrome_version)
    if managed_download:
        return managed_download

    raise RuntimeError(
        f"no matching local chromedriver found for Chrome {chrome_version}. "
        "Please check network access or set CHROMEDRIVER_PATH to a matching binary."
    )


def _resolve_chrome_binary() -> str:
    env_val = os.getenv("CHROME_BINARY", "").strip()
    if env_val:
        p = Path(env_val)
        if p.exists() and p.is_file():
            return str(p)
        found = shutil.which(env_val)
        if found:
            return found

    mac_candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    for candidate in mac_candidates:
        p = Path(candidate)
        if p.exists() and p.is_file():
            return str(p)

    for name in ["google-chrome", "chromium-browser", "chromium"]:
        found = shutil.which(name)
        if found:
            return found
    return ""


def _resolve_chrome_version() -> str:
    chrome_binary = _resolve_chrome_binary()
    if not chrome_binary:
        raise RuntimeError("chrome binary not found")
    try:
        proc = subprocess.run([chrome_binary, "--version"], capture_output=True, text=True, timeout=5)
        output = f"{proc.stdout}\n{proc.stderr}".strip()
    except Exception as exc:
        raise RuntimeError(f"failed to read Chrome version: {exc}") from exc
    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
    if not match:
        raise RuntimeError(f"unable to parse Chrome version from: {output}")
    return match.group(1)


def _version_major(version: str) -> int | None:
    match = re.match(r"(\d+)\.", version or "")
    return int(match.group(1)) if match else None


def _binary_major_version(path: str) -> int | None:
    normalized = _normalize_executable_path(path)
    if not normalized:
        return None
    try:
        proc = subprocess.run([normalized, "--version"], capture_output=True, text=True, timeout=5)
        output = f"{proc.stdout}\n{proc.stderr}".strip()
    except Exception:
        return None
    return _version_major(output)


def _normalize_executable_path(candidate: str) -> str:
    candidate = (candidate or "").strip()
    if not candidate:
        return ""
    path = Path(candidate)
    if path.exists() and path.is_file():
        return str(path)
    found = shutil.which(candidate)
    return found or ""


def _chromedriver_platform_label() -> str:
    system = platform.system().lower()
    machine = _system_machine_arch()
    if system == "darwin":
        return "mac-arm64" if machine in {"arm64", "aarch64"} else "mac-x64"
    if system == "linux":
        return "linux64"
    if system == "windows":
        return "win64" if "64" in machine else "win32"
    raise RuntimeError(f"unsupported platform for managed chromedriver: {system}/{machine}")


def _system_machine_arch() -> str:
    try:
        proc = subprocess.run(["uname", "-m"], capture_output=True, text=True, timeout=5)
        arch = (proc.stdout or "").strip().lower()
        if arch:
            return arch
    except Exception:
        pass
    return platform.machine().lower()


def _managed_driver_binary_path(version: str) -> Path:
    platform_label = _chromedriver_platform_label()
    binary_name = "chromedriver.exe" if platform_label.startswith("win") else "chromedriver"
    return MANAGED_CHROMEDRIVER_DIR / platform_label / version / f"chromedriver-{platform_label}" / binary_name


def _find_cached_managed_chromedriver(chrome_major: int) -> str:
    platform_label = _chromedriver_platform_label()
    platform_dir = MANAGED_CHROMEDRIVER_DIR / platform_label
    if not platform_dir.exists():
        return ""
    versions = sorted(
        [path for path in platform_dir.iterdir() if path.is_dir()],
        key=lambda item: item.name,
        reverse=True,
    )
    for version_dir in versions:
        if _version_major(version_dir.name) != chrome_major:
            continue
        candidate = _managed_driver_binary_path(version_dir.name)
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return ""


def _ensure_managed_chromedriver(chrome_version: str) -> str:
    cached = _managed_driver_binary_path(chrome_version)
    if cached.exists() and cached.is_file():
        print(f"using cached managed chromedriver: {cached}")
        return str(cached)

    target_version = chrome_version
    download_error = None
    for candidate_version in [chrome_version, _resolve_latest_patch_version(chrome_version)]:
        if not candidate_version:
            continue
        try:
            return _download_managed_chromedriver(candidate_version)
        except Exception as exc:
            download_error = exc
            continue

    if download_error:
        raise RuntimeError(f"failed to download managed chromedriver for Chrome {target_version}: {download_error}") from download_error
    return ""


def _resolve_latest_patch_version(chrome_version: str) -> str:
    build_key = ".".join((chrome_version or "").split(".")[:3])
    if not build_key:
        return ""
    with urllib.request.urlopen(CFT_LATEST_PATCH_VERSIONS_URL, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    builds = payload.get("builds", {})
    build_meta = builds.get(build_key, {})
    return build_meta.get("version", "")


def _download_managed_chromedriver(version: str) -> str:
    platform_label = _chromedriver_platform_label()
    zip_url = f"{CFT_DOWNLOAD_BASE_URL}/{version}/{platform_label}/chromedriver-{platform_label}.zip"
    target_binary = _managed_driver_binary_path(version)
    target_dir = target_binary.parent.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / f"chromedriver-{platform_label}.zip"

    print(f"downloading managed chromedriver {version} for {platform_label} -> {target_dir}")
    last_error = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(zip_url, timeout=30) as response, zip_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(target_dir)
            if not target_binary.exists():
                raise RuntimeError(f"chromedriver binary missing after extract: {target_binary}")
            break
        except Exception as exc:
            last_error = exc
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                shutil.rmtree(target_binary.parent, ignore_errors=True)
            except Exception:
                pass
            if attempt >= 3:
                raise
            print(f"managed chromedriver download retry {attempt}/3 failed: {exc}")
            time.sleep(min(1.5 * attempt, 3))
    if last_error and not zip_path.exists():
        raise last_error
    try:
        current_mode = target_binary.stat().st_mode
        target_binary.chmod(current_mode | 0o111)
    except Exception:
        pass
    try:
        zip_path.unlink(missing_ok=True)
    except Exception:
        pass
    return str(target_binary)


def _ensure_runtime_env_defaults() -> None:
    """Set sane local defaults for speed and persistent login state."""

    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")
    os.environ.setdefault("DOWNLOAD_REUSE_DRIVER", "1")
    os.environ.setdefault("CHROME_HEADLESS_MODE", "new")
    os.environ.setdefault("USE_LOCAL_CHROMEDRIVER", "1")
    if not os.getenv("CHROME_USER_DATA_DIR", "").strip():
        DEFAULT_FUTU_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        os.environ["CHROME_USER_DATA_DIR"] = str(DEFAULT_FUTU_PROFILE_DIR)

    
def _safe_quit_driver(driver) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _build_chrome_options(headless_mode: str) -> Options:
    chrome_binary = _resolve_chrome_binary()
    chrome_options = Options()
    if chrome_binary:
        chrome_options.binary_location = chrome_binary

    mode = (headless_mode or "").strip().lower()
    interactive_login = os.getenv("FUTU_INTERACTIVE_LOGIN", "").strip().lower() in {"1", "true", "yes", "on"} or mode == "off"
    if mode in {"new", "old"}:
        if mode == "new":
            chrome_options.add_argument("--headless=new")
        else:
            chrome_options.add_argument("--headless")
    # mode == "off" -> visible browser

    user_data_dir = os.getenv("CHROME_USER_DATA_DIR", "").strip()
    profile_dir = os.getenv("CHROME_PROFILE_DIR", "").strip()
    if user_data_dir:
        chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
    if profile_dir:
        chrome_options.add_argument(f"--profile-directory={profile_dir}")

    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1440,960" if interactive_login else "--window-size=1280,720")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-sync")
    chrome_options.add_argument("--metrics-recording-only")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--lang=zh-CN")
    chrome_options.add_argument("--disable-features=TranslateUI,BackForwardCache")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--remote-debugging-pipe")
    if interactive_login:
        chrome_options.add_argument("--start-maximized")
    else:
        chrome_options.add_argument("--disable-background-networking")
        chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    # Linux stability: avoid aggressive flags like --single-process, which may trigger renderer disconnects.
    if platform.system() == "Linux":
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--disable-ipc-flooding-protection")
        chrome_options.add_argument("--memory-pressure-off")
    extra_args = os.getenv("CHROME_EXTRA_ARGS", "").strip()
    if extra_args:
        for token in extra_args.split():
            if token:
                chrome_options.add_argument(token)

    chrome_options.page_load_strategy = "normal" if interactive_login else "eager"
    prefs = {"download.default_directory": localPath}
    if not interactive_login:
        prefs.update(
            {
                "profile.default_content_setting_values.images": 2,
                "profile.managed_default_content_settings.images": 2,
            }
        )
    chrome_options.add_experimental_option("prefs", prefs)
    return chrome_options


def _build_driver():
    headless_mode = os.getenv("CHROME_HEADLESS_MODE", "auto").strip().lower()
    mode_candidates = [headless_mode]
    if headless_mode == "auto":
        mode_candidates = ["new", "old"]
    last_exc = None
    for mode in mode_candidates:
        try:
            service = None
            try:
                driver_path = _resolve_chromedriver_path()
                service = Service(executable_path=driver_path)
            except Exception as exc:
                last_exc = exc
            if service is None:
                raise RuntimeError("local chromedriver unavailable")

            driver = webdriver.Chrome(service=service, options=_build_chrome_options(mode))
            driver.set_page_load_timeout(60)
            # Futu pages can execute heavy frontend scripts; avoid false script-timeout aborts.
            driver.set_script_timeout(30)
            return driver
        except Exception as exc:
            last_exc = exc
            path_backup = os.environ.get("PATH", "")
            try:
                manager_options = _build_chrome_options(mode)
                try:
                    local_driver_path = _resolve_chromedriver_path()
                except Exception:
                    local_driver_path = ""
                if local_driver_path:
                    blocked_dir = str(Path(local_driver_path).resolve().parent)
                    path_entries = [item for item in path_backup.split(os.pathsep) if item and Path(item).resolve() != Path(blocked_dir)]
                    os.environ["PATH"] = os.pathsep.join(path_entries)
                if _driver_version_mismatch(exc):
                    print(f"chromedriver version mismatch detected, fallback to selenium manager in mode={mode}: {exc}")
                else:
                    print(f"local chromedriver unavailable, fallback to selenium manager in mode={mode}: {exc}")
                driver = webdriver.Chrome(service=Service(), options=manager_options)
                driver.set_page_load_timeout(60)
                # Futu pages can execute heavy frontend scripts; avoid false script-timeout aborts.
                driver.set_script_timeout(30)
                return driver
            except Exception as manager_exc:
                last_exc = manager_exc
                if headless_mode != "auto":
                    raise
                print(f"build driver fallback failed in mode={mode}: {manager_exc}")
                time.sleep(0.2)
            finally:
                os.environ["PATH"] = path_backup
    raise last_exc if last_exc else RuntimeError("failed to build chrome driver")


def _driver_version_mismatch(exc: Exception) -> bool:
    if isinstance(exc, SessionNotCreatedException):
        msg = str(exc).lower()
        return "only supports chrome version" in msg or "current browser version is" in msg
    msg = str(exc).lower()
    return "only supports chrome version" in msg and "current browser version is" in msg


def _requires_driver_recreate(exc: Exception) -> bool:
    msg = str(exc).lower()
    patterns = [
        "invalid session id",
        "session deleted",
        "browser has closed the connection",
        "from disconnected",
        "unable to receive message from renderer",
        "disconnected: unable to receive message from renderer",
        "tab crashed",
    ]
    return any(p in msg for p in patterns)


def _driver_get_with_retry(driver, url: str, retry: int = 2) -> None:
    last_exc = None
    for i in range(1, retry + 1):
        try:
            driver.get(url)
            return
        except WebDriverException as exc:
            last_exc = exc
            if _requires_driver_recreate(exc):
                raise
            if not _is_recoverable_driver_error(exc):
                raise
            time.sleep(min(1.2 * i, 3))
            try:
                driver.get("about:blank")
            except Exception:
                pass
    raise last_exc if last_exc else RuntimeError(f"failed to open url: {url}")


def _is_recoverable_driver_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    patterns = [
        "tab crashed",
        "session deleted because of page crash",
        "invalid session id",
        "browser has closed the connection",
        "unable to receive message from renderer",
        "from disconnected",
        "disconnected: unable to receive message from renderer",
    ]
    return any(p in msg for p in patterns)


def _normalize_period(raw: str) -> str:
    return re.sub(r'^\(FY\)|^\(Q\d+\)', '', raw).strip()


def _safe_click(driver, element) -> bool:
    try:
        element.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            element.click()
            return True
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False


def _click_first_visible_xpath(driver, xpaths, timeout=3) -> bool:
    for xp in xpaths:
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            if el.is_displayed():
                if _safe_click(driver, el):
                    return True
        except TimeoutException:
            continue
        except Exception:
            continue
    return False


def _click_xpath_candidates(driver, xpaths, timeout=2) -> bool:
    for xp in xpaths:
        try:
            els = WebDriverWait(driver, timeout).until(
                EC.presence_of_all_elements_located((By.XPATH, xp))
            )
            for el in els[:3]:
                if el.is_displayed() and _safe_click(driver, el):
                    return True
        except Exception:
            continue
    return False


def _get_period_dropdown_text(driver) -> str:
    xpaths = [
        "/html/body/div[2]/div/div[1]/section/div/section/section/section/div[1]/div[1]",
        "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/div[1]/div[1]",
        "(//*[self::button or self::div or self::span][contains(.,'全部') or contains(.,'年报') or contains(.,'年報') or contains(.,'Annual') or contains(.,'All') or contains(.,'Interim') or contains(.,'Q1') or contains(.,'Q3')])[1]",
    ]
    for xp in xpaths:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els[:2]:
                txt = (el.text or "").strip()
                if txt and el.is_displayed():
                    return txt
        except Exception:
            continue
    return ""


def _is_annual_selected(driver) -> bool:
    t = _get_period_dropdown_text(driver).lower()
    if not t:
        return False
    return ("年报" in t) or ("年報" in t) or ("annual" in t) or ("yearly" in t)


def _select_annual_from_open_menu(driver) -> bool:
    # 1) Exact/contains label match (CN + EN)
    label_xpaths = [
        "//*[self::li or self::div or self::span or self::button][normalize-space()='年报']",
        "//*[self::li or self::div or self::span or self::button][normalize-space()='年報']",
        "//*[self::li or self::div or self::span or self::button][normalize-space()='Annual']",
        "//*[self::li or self::div or self::span or self::button][normalize-space()='Yearly']",
        "//*[self::li or self::div or self::span or self::button][contains(normalize-space(.),'年报')]",
        "//*[self::li or self::div or self::span or self::button][contains(normalize-space(.),'年報')]",
        "//*[self::li or self::div or self::span or self::button][contains(normalize-space(.),'Annual')]",
        "//*[self::li or self::div or self::span or self::button][contains(normalize-space(.),'Yearly')]",
    ]
    if _click_xpath_candidates(driver, label_xpaths, timeout=1):
        time.sleep(0.08)
        if _is_annual_selected(driver):
            return True

    # 2) Fallback: click the 2nd visible option in latest popup/list.
    option_groups = []
    for xp in (
        "(//*[@role='listbox'])[last()]//*[@role='option' or self::li or self::div or self::span][normalize-space()]",
        "(//ul[.//li])[last()]//li[normalize-space()]",
        "(//div[contains(@class,'menu') or contains(@class,'dropdown') or contains(@class,'select')])[last()]//*[self::li or self::div or self::span][normalize-space()]",
    ):
        try:
            els = driver.find_elements(By.XPATH, xp)
            visible = [e for e in els if e.is_displayed()]
            if visible:
                option_groups.append(visible)
        except Exception:
            continue

    for opts in option_groups:
        # Debug preview disabled by default to keep logs clean.
        preview = [o.text.strip() for o in opts[:6]]
        if len(opts) >= 2 and _safe_click(driver, opts[1]):
            time.sleep(0.08)
            if _is_annual_selected(driver):
                return True

    # 3) Final fallback: keyboard select next option.
    try:
        ae = driver.switch_to.active_element
        ae.send_keys(Keys.ARROW_DOWN)
        time.sleep(0.05)
        ae.send_keys(Keys.ENTER)
        time.sleep(0.08)
        return _is_annual_selected(driver)
    except Exception:
        return False


def _select_report_period_annual(driver) -> bool:
    """
    Force-select 年报 from the report-period dropdown (not 全部/季度).
    """
    # 1) Try direct click when menu is already open.
    if _click_first_visible_xpath(
        driver,
        [
            "//li[normalize-space()='年报']",
            "//li[normalize-space()='年報']",
            "//*[@role='menuitem' and normalize-space()='年报']",
            "//*[@role='menuitem' and normalize-space()='年報']",
            "//div[contains(@class,'menu') and normalize-space()='年报']",
            "//div[contains(@class,'menu') and normalize-space()='年報']",
            "//span[normalize-space()='年报']",
            "//span[normalize-space()='年報']",
        ],
        timeout=1,
    ):
        return True

    # 2) Open period dropdown near top controls, then click 年报.
    open_clicked = _click_first_visible_xpath(
        driver,
        [
            "(//button[contains(.,'全部') or contains(.,'年报') or contains(.,'年報') or contains(.,'中报') or contains(.,'中報') or contains(.,'一季报') or contains(.,'一季報') or contains(.,'三季报') or contains(.,'三季報')])[1]",
            "(//div[contains(.,'全部') or contains(.,'年报') or contains(.,'年報') or contains(.,'中报') or contains(.,'中報') or contains(.,'一季报') or contains(.,'一季報') or contains(.,'三季报') or contains(.,'三季報')])[1]",
            "(//span[contains(.,'全部') or contains(.,'年报') or contains(.,'年報') or contains(.,'中报') or contains(.,'中報') or contains(.,'一季报') or contains(.,'一季報') or contains(.,'三季报') or contains(.,'三季報')])[1]",
        ],
        timeout=1,
    )
    if open_clicked:
        time.sleep(0.12)
        if _click_first_visible_xpath(
            driver,
            [
                "//li[normalize-space()='年报']",
                "//li[normalize-space()='年報']",
                "//*[@role='menuitem' and normalize-space()='年报']",
                "//*[@role='menuitem' and normalize-space()='年報']",
                "//div[normalize-space()='年报']",
                "//div[normalize-space()='年報']",
                "//span[normalize-space()='年报']",
                "//span[normalize-space()='年報']",
            ],
            timeout=1,
        ):
            return True
        if _select_annual_from_open_menu(driver):
            return True

    return False


def _set_toggle_state_by_label(driver, label_text: str, target_on: bool) -> bool:
    """
    Try to find a switch/checkbox near text label and force state.
    target_on=False means "unchecked/off".
    Selenium-only implementation to avoid JS script timeouts.
    """
    try:
        labels = driver.find_elements(
            By.XPATH,
            (
                f"//*[self::label or self::span or self::div or self::button]"
                f"[contains(normalize-space(.), '{label_text}')]"
            ),
        )
        for label in labels[:6]:
            if not label.is_displayed():
                continue
            control = None
            search_paths = [
                ".//following::*[@role='switch'][1]",
                ".//following::*[@aria-checked][1]",
                ".//following::input[@type='checkbox'][1]",
                ".//ancestor::*[1]//*[@role='switch']",
                ".//ancestor::*[1]//*[@aria-checked]",
                ".//ancestor::*[1]//input[@type='checkbox']",
            ]
            for sp in search_paths:
                try:
                    cand = label.find_element(By.XPATH, sp)
                    if cand and cand.is_displayed():
                        control = cand
                        break
                except Exception:
                    continue
            if control is None:
                continue

            is_on = False
            try:
                if (control.get_attribute("type") or "").lower() == "checkbox":
                    is_on = bool(control.is_selected())
                else:
                    aria_checked = control.get_attribute("aria-checked")
                    if aria_checked is not None:
                        is_on = str(aria_checked).lower() == "true"
                    else:
                        cls = str(control.get_attribute("class") or "")
                        is_on = bool(re.search(r"(checked|active|selected|on|open)", cls, re.I))
            except Exception:
                is_on = False

            if is_on != target_on:
                if not _safe_click(driver, control):
                    continue
            return True
        return False
    except TimeoutException:
        return False
    except Exception:
        return False


def _dismiss_common_popups(driver) -> None:
    candidates = [
        "//button[contains(.,'同意')]",
        "//button[contains(.,'接受')]",
        "//button[contains(.,'Agree')]",
        "//button[contains(.,'Accept')]",
        "//span[contains(.,'同意')]",
        "//div[contains(@class,'close')]",
        "//button[contains(@class,'close')]",
    ]
    for xp in candidates:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els[:2]:
                if el.is_displayed():
                    _safe_click(driver, el)
        except Exception:
            continue


def _wait_report_controls_ready(driver, timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _dismiss_common_popups(driver)
        except Exception:
            pass
        # Report period controls should appear before extraction (CN + EN).
        for xp in (
            "//button[contains(.,'年报') or contains(.,'年報') or contains(.,'全部') or contains(.,'中报') or contains(.,'中報') or contains(.,'Annual') or contains(.,'All') or contains(.,'Interim') or contains(.,'Q1') or contains(.,'Q3')]",
            "//div[contains(.,'年报') or contains(.,'年報') or contains(.,'全部') or contains(.,'中报') or contains(.,'中報') or contains(.,'Annual') or contains(.,'All') or contains(.,'Interim') or contains(.,'Q1') or contains(.,'Q3')]",
            "//span[contains(.,'年报') or contains(.,'年報') or contains(.,'全部') or contains(.,'中报') or contains(.,'中報') or contains(.,'Annual') or contains(.,'All') or contains(.,'Interim') or contains(.,'Q1') or contains(.,'Q3')]",
        ):
            try:
                els = driver.find_elements(By.XPATH, xp)
                if any(el.is_displayed() for el in els[:4]):
                    return True
            except Exception:
                continue
        time.sleep(0.25)
    return False


def _wait_financial_content_ready(driver, timeout: int = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _dismiss_common_popups(driver)
        except Exception:
            pass
        try:
            if driver.find_elements(By.CSS_SELECTOR, "table thead th"):
                return True
        except Exception:
            pass
        try:
            page = driver.page_source
            if re.search(r"\d{4}/\d{2}/\d{2}", page):
                return True
        except Exception:
            pass
        for xp in (
            "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/section[1]",
            "/html/body/div[2]/div/div[1]/section/div/section/section/section/section[1]",
        ):
            try:
                t = driver.find_element(By.XPATH, xp).text
                if re.search(r"\d{4}/\d{2}/\d{2}", t):
                    return True
            except Exception:
                continue
        time.sleep(0.3)
    return False


def _prepare_financial_table_view(driver, yearCycle: bool = True) -> None:
    """
    Ensure the page is in annual mode and YoY/Hide-empty toggles are off,
    so extracted data is complete and clean.
    """
    if yearCycle:
        try:
            driver.execute_script("window.scrollTo(0, 0);")
        except Exception:
            pass
        open_ok = _click_xpath_candidates(
            driver,
            [
                "/html/body/div[2]/div/div[1]/section/div/section/section/section/div[1]/div[1]",
                "/html/body/div[2]/div/div[1]/section/div/section/section/section/div[1]/div[1]/span",
                "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/div[1]/div[1]",
                "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/div[1]/div[1]/span",
                "(//*[self::button or self::div or self::span][contains(.,'全部') or contains(.,'年报') or contains(.,'年報') or contains(.,'Annual') or contains(.,'All')])[1]",
            ],
            timeout=2,
        )
        if open_ok:
            time.sleep(0.15)
            annual_ok = _click_xpath_candidates(
                driver,
                [
                    "/html/body/div[2]/div/div[1]/section/div/section/section/section/div[1]/div[1]/div/div[2]",
                    "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/div[1]/div[1]/div/div[2]",
                    "//li[normalize-space()='年报']",
                    "//li[normalize-space()='年報']",
                    "//*[@role='menuitem' and normalize-space()='年报']",
                    "//*[@role='menuitem' and normalize-space()='年報']",
                    "//*[self::li or self::button or self::div or self::span][normalize-space()='Annual']",
                    "//*[self::li or self::button or self::div or self::span][normalize-space()='Yearly']",
                ],
                timeout=2,
            )
            if not annual_ok:
                annual_ok = _select_annual_from_open_menu(driver)
        else:
            annual_ok = False

        if not annual_ok:
            annual_ok = _select_report_period_annual(driver)
        annual_state = _is_annual_selected(driver)
        annual_ok = annual_ok and annual_state
        if os.getenv("FINANCE_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}:
            print(
                f"action period annual: open={open_ok} annual={annual_ok} "
                f"period_text='{_get_period_dropdown_text(driver)}'"
            )

    # Disable "显示同比" and "隐藏空行" to avoid duplicated/filtered rows.
    yoy_clicked = _click_xpath_candidates(
        driver,
        [
            "/html/body/div[2]/div/div[1]/section/div/section/section/section/div[1]/div[2]",
            "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/div[1]/div[2]",
            "(//*[self::div or self::span or self::button][contains(.,'显示同比') or contains(.,'顯示同比') or contains(.,'YoY') or contains(.,'同比')])[1]",
        ],
        timeout=1,
    )
    hide_clicked = _click_xpath_candidates(
        driver,
        [
            "/html/body/div[2]/div/div[1]/section/div/section/section/section/div[1]/div[3]",
            "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/div[1]/div[3]",
            "(//*[self::div or self::span or self::button][contains(.,'隐藏空行') or contains(.,'隱藏空行') or contains(.,'Hide')])[1]",
        ],
        timeout=1,
    )
    yoy_ok = (
        _set_toggle_state_by_label(driver, "显示同比", False)
        or _set_toggle_state_by_label(driver, "顯示同比", False)
        or _set_toggle_state_by_label(driver, "YoY", False)
        or _set_toggle_state_by_label(driver, "Show YoY", False)
    )
    hide_ok = (
        _set_toggle_state_by_label(driver, "隐藏空行", False)
        or _set_toggle_state_by_label(driver, "隱藏空行", False)
        or _set_toggle_state_by_label(driver, "Hide empty rows", False)
        or _set_toggle_state_by_label(driver, "Hide Empty Rows", False)
    )
    print(f"action toggles: yoy_click={yoy_clicked} hide_click={hide_clicked} yoy_ok={yoy_ok} hide_ok={hide_ok}")

    time.sleep(0.35)


def _headers_snapshot_from_page(driver) -> list[str]:
    try:
        tables = pd.read_html(StringIO(driver.page_source))
    except Exception:
        return []
    if not tables:
        return []
    candidate = max(tables, key=lambda t: t.shape[1])
    if candidate.shape[1] < 2:
        return []
    return [str(x).strip() for x in candidate.columns[1:]]


def _looks_annual_mode(headers: list[str]) -> bool:
    if not headers:
        return False
    fy_count = sum(1 for h in headers if str(h).strip().startswith("(FY)"))
    if fy_count >= 3:
        return True

    parsed = []
    for h in headers:
        key = _period_key(str(h).strip())
        if not re.match(r"^\d{4}/\d{2}/\d{2}$", key):
            continue
        try:
            dt = pd.to_datetime(key, format="%Y/%m/%d", errors="raise")
        except Exception:
            continue
        parsed.append(dt)
    if len(parsed) < 3:
        return False

    by_md = {}
    for dt in parsed:
        by_md.setdefault((dt.month, dt.day), set()).add(dt.year)
    if max(len(years) for years in by_md.values()) >= 3:
        return True

    parsed = sorted(parsed, reverse=True)
    yearly_steps = 0
    for i in range(len(parsed) - 1):
        if abs((parsed[i] - parsed[i + 1]).days) >= 300:
            yearly_steps += 1
    return yearly_steps >= 2


def _ensure_clean_annual_view(driver, yearCycle: bool, rounds: int = 3) -> None:
    """
    Hard-enforce annual + non-YoY + non-hidden-empty-row state before extraction.
    """
    for _ in range(max(1, rounds)):
        _prepare_financial_table_view(driver, yearCycle=yearCycle)
        headers = _headers_snapshot_from_page(driver)
        if not headers:
            time.sleep(0.25)
            continue
        has_yoy = any(_is_yoy_header(h) for h in headers)
        if yearCycle:
            if _looks_annual_mode(headers) and not has_yoy:
                return
        else:
            if not has_yoy:
                return
        time.sleep(0.25)
    # Continue with best-effort extraction, but record a warning.
    headers = _headers_snapshot_from_page(driver)
    print(
        "Warning: view state may be imperfect "
        f"(yearCycle={yearCycle}, annual={_looks_annual_mode(headers)}, yoy={any(_is_yoy_header(h) for h in headers)})"
    )


def _is_yoy_header(text: str) -> bool:
    t = str(text).strip().lower()
    return (
        "同比" in t
        or "顯示同比" in t
        or "yoy" in t
        or "growth" in t
        or "change" in t
        or "环比" in t
        or "按年" in t
    )


def _period_key(text: str) -> str:
    return re.sub(r"\.\d+$", "", _normalize_period(str(text).strip()))


def _is_period_like_header(text: str) -> bool:
    t = _period_key(str(text).strip())
    return bool(
        t.startswith("(FY)")
        or re.match(r"^\d{4}/\d{2}/\d{2}$", t)
        or "ttm" in t.lower()
    )


def _annual_column_count(headers: list[str]) -> int:
    count = 0
    for h in headers:
        hs = str(h).strip()
        if hs.startswith("(FY)"):
            count += 1
            continue
        key = _period_key(hs)
        if re.match(r"^\d{4}/\d{2}/\d{2}$", key):
            count += 1
    return count


def _is_percent_only_cell(text: str) -> bool:
    s = str(text).strip().replace(",", "")
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?%", s))


def _amount_like_score(series: pd.Series) -> float:
    vals = series.fillna("--").astype(str).str.strip()
    if len(vals) == 0:
        return -1e9
    non_empty = vals[~vals.isin(["", "--", "----", "nan", "None"])]
    if len(non_empty) == 0:
        return -1e9

    pct_only = non_empty.map(_is_percent_only_cell).sum()
    numeric_like = non_empty.str.contains(r"-?\d", regex=True).sum()
    monetary_like = non_empty.str.contains(r"(?:萬億|億|萬|,)", regex=True).sum()
    return float(numeric_like + monetary_like - 2 * pct_only)


def _clean_period_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    cols = [str(c).strip() for c in df.columns]
    keep_idx = [i for i, c in enumerate(cols) if not _is_yoy_header(c)]
    if keep_idx:
        work = df.iloc[:, keep_idx].copy()
        cols = [cols[i] for i in keep_idx]
    else:
        work = df.copy()

    grouped = {}
    key_order = []
    for i, c in enumerate(cols):
        key = _period_key(c)
        if key not in grouped:
            grouped[key] = []
            key_order.append(key)
        grouped[key].append(i)

    selected_idx = []
    selected_keys = []
    for key in key_order:
        idx_list = grouped[key]
        if len(idx_list) == 1:
            pick = idx_list[0]
        else:
            # Prefer amount-like column; if tie keep left-most.
            pick = max(idx_list, key=lambda j: (_amount_like_score(work.iloc[:, j]), -j))
        selected_idx.append(pick)
        selected_keys.append(key)

    out = work.iloc[:, selected_idx].copy()
    out.columns = selected_keys
    return out


def _extract_value_only(raw: str) -> str:
    text = str(raw).strip()
    if text.lower() == "nan" or text in {"", "--", "----"}:
        return "--"
    m = re.match(r"^\s*-?\d+(?:\.\d+)?%\s*(.+)$", text)
    if m:
        value = m.group(1).strip()
        return value if value else "--"
    return text


def _patch_rows_for_period(lines: list[str], period_count: int) -> list[str]:
    """
    Patch missing cells so each row has (1 + period_count) fields.
    """
    if period_count <= 0:
        return lines
    patched = list(lines)
    index = []
    for i in range(len(patched)):
        if (re.search(r"\d", patched[i]) is None) and ("--" not in patched[i]):
            index.append(i)
    if not index:
        return patched
    index.append(len(patched))
    row_len = period_count + 1
    for j in range(len(index) - 1):
        delta = index[j + 1] - index[j]
        for _ in range(max(0, row_len - delta)):
            patched.insert(j * row_len + delta, "--")
    return patched


def _scroll_all_horizontal_containers(driver, ratio: float, max_nodes: int = 24) -> int:
    return int(
        driver.execute_script(
            """
            const ratio = Math.max(0, Math.min(1, arguments[0]));
            const maxNodes = arguments[1];
            const isVisible = (el) => {
              const r = el.getBoundingClientRect();
              return r.width > 80 && r.height > 20 && r.bottom > 0 && r.top < window.innerHeight;
            };
            const nodes = Array.from(document.querySelectorAll('div,section,main,article'))
              .filter(el => isVisible(el))
              .filter(el => (el.scrollWidth - el.clientWidth) > 40)
              .sort((a, b) => (b.scrollWidth - b.clientWidth) - (a.scrollWidth - a.clientWidth))
              .slice(0, maxNodes);
            for (const el of nodes) {
              const max = Math.max(0, el.scrollWidth - el.clientWidth);
              el.scrollLeft = Math.floor(max * ratio);
            }
            return nodes.length;
            """,
            float(ratio),
            int(max_nodes),
        )
        or 0
    )


def _reset_horizontal_scroll(driver, max_nodes: int = 24) -> None:
    driver.execute_script(
        """
        const maxNodes = arguments[0];
        const nodes = Array.from(document.querySelectorAll('div,section,main,article'))
          .filter(el => (el.scrollWidth - el.clientWidth) > 40)
          .slice(0, maxNodes);
        for (const el of nodes) el.scrollLeft = 0;
        """,
        int(max_nodes),
    )


def _extract_table_via_section_text(driver) -> pd.DataFrame:
    """
    Read full statement text blocks (header section + body section), not just visible viewport cells.
    This usually includes more historical columns than DOM-viewport extraction.
    """
    header_xpaths = [
        "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/section[1]",
        "/html/body/div[2]/div/div[1]/section/div/section/section/section/section[1]",
    ]
    body_xpaths = [
        "/html/body/div[2]/div[2]/div[2]/div[1]/section/div/section/section/section/section[2]",
        "/html/body/div[2]/div/div[1]/section/div/section/section/section/section[2]",
    ]

    header_text = ""
    body_text = ""
    for hp in header_xpaths:
        try:
            header_text = driver.find_element(By.XPATH, hp).text.strip()
            if header_text:
                break
        except Exception:
            continue
    for bp in body_xpaths:
        try:
            body_text = driver.find_element(By.XPATH, bp).text.strip()
            if body_text:
                break
        except Exception:
            continue
    if not header_text or not body_text:
        raise RuntimeError("section-text extraction failed")

    headers = [x.strip() for x in header_text.replace("(FY)", "").splitlines() if x.strip()]
    # Keep only date-like headers to avoid pollution from control text.
    headers = [h for h in headers if re.match(r"^\d{4}/\d{2}/\d{2}$", h)]
    if len(headers) < 3:
        raise RuntimeError("section-text headers insufficient")

    lines = [x.strip() for x in body_text.splitlines() if x.strip()]
    lines = _patch_rows_for_period(lines, len(headers))
    row_len = len(headers) + 1
    chunks = []
    for i in range(0, len(lines), row_len):
        chunk = lines[i:i + row_len]
        if len(chunk) == row_len:
            chunks.append(chunk)
    if not chunks:
        raise RuntimeError("section-text rows empty")

    arr = np.array(chunks, dtype=object)
    idx = [str(x).strip() for x in arr[:, 0]]
    vals = arr[:, 1:]
    frame = pd.DataFrame(vals, index=idx, columns=headers)
    return frame


def _extract_table_via_section_text_scrolled(driver, max_steps: int = 14) -> pd.DataFrame:
    frames = []
    seen = set()
    ratios = np.linspace(0.0, 1.0, num=max(3, max_steps))
    for ratio in ratios:
        try:
            _scroll_all_horizontal_containers(driver, float(ratio))
        except Exception:
            pass
        time.sleep(0.12)
        try:
            fr = _extract_table_via_section_text(driver)
            sig = tuple(str(c).strip() for c in fr.columns)
            if sig and sig not in seen:
                seen.add(sig)
                frames.append(fr)
        except Exception:
            continue
    try:
        _reset_horizontal_scroll(driver)
    except Exception:
        pass
    if not frames:
        raise RuntimeError("section-text scrolled extraction failed")
    return _merge_dom_frames(frames)


def _extract_table_via_read_html(driver) -> pd.DataFrame:
    # Prefer lxml parser to avoid html5lib dependency on servers.
    tables = pd.read_html(StringIO(driver.page_source), flavor="lxml")
    if not tables:
        raise RuntimeError("read_html returned no tables")
    candidate = max(tables, key=lambda t: t.shape[1])
    if candidate.empty or candidate.shape[1] < 2:
        raise RuntimeError("read_html table invalid")
    headers = [str(x).strip() for x in candidate.columns[1:]]
    index_names = candidate.iloc[:, 0].astype(str).str.strip()
    values = candidate.iloc[:, 1:].copy()
    frame = pd.DataFrame(values.to_numpy(), index=index_names, columns=headers)
    frame = frame[frame.index != ""]
    frame = frame[~frame.index.duplicated(keep='first')]
    return frame


def _pick_annual_date_columns(cols: list[str]) -> list[str]:
    parsed: list[tuple[int, pd.Timestamp, str]] = []
    for i, c in enumerate(cols):
        text = str(c).strip()
        if not re.match(r"^\d{4}/\d{2}/\d{2}$", text):
            continue
        try:
            dt = pd.to_datetime(text, format="%Y/%m/%d", errors="raise")
        except Exception:
            continue
        parsed.append((i, dt, text))
    if len(parsed) == 0:
        return []
    if len(parsed) <= 2:
        return [x[2] for x in parsed]

    # Prefer the month/day chain with the largest year coverage.
    by_md: dict[tuple[int, int], list[tuple[int, pd.Timestamp, str]]] = {}
    for item in parsed:
        by_md.setdefault((item[1].month, item[1].day), []).append(item)
    best_md_items = max(by_md.values(), key=lambda items: len({x[1].year for x in items}))
    best_md_years = len({x[1].year for x in best_md_items})
    if best_md_years >= 3:
        best_md_items = sorted(best_md_items, key=lambda x: x[0])
        return [x[2] for x in best_md_items]

    # Fallback: keep points roughly one year apart from most recent column.
    kept = [parsed[0]]
    last_dt = parsed[0][1]
    for item in parsed[1:]:
        delta_days = abs((last_dt - item[1]).days)
        if delta_days >= 300:
            kept.append(item)
            last_dt = item[1]
    kept = sorted(kept, key=lambda x: x[0])
    return [x[2] for x in kept]


def _latest_period_from_columns(cols: list[str]) -> str:
    latest = None
    for c in cols:
        key = _period_key(str(c).strip())
        if not re.match(r"^\d{4}/\d{2}/\d{2}$", key):
            continue
        try:
            dt = pd.to_datetime(key, format="%Y/%m/%d", errors="raise")
        except Exception:
            continue
        if latest is None or dt > latest:
            latest = dt
    return "" if latest is None else latest.strftime("%Y/%m/%d")


def _latest_period_from_local_csv(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        df = pd.read_csv(path, index_col=0, encoding='utf-8-sig')
    except Exception:
        return ""
    return _latest_period_from_columns([str(x) for x in df.columns])


def _looks_like_login_gate(driver) -> bool:
    try:
        body = (driver.find_element(By.TAG_NAME, "body").text or "").strip()
    except Exception:
        return False
    if not body:
        return False
    markers = [
        "註冊/登入",
        "注册/登录",
        "登入",
        "登录",
        "log in",
        "sign in",
    ]
    has_login = any(m.lower() in body.lower() for m in markers)
    has_table_signal = bool(re.search(r"\d{4}/\d{2}/\d{2}", body)) or ("年报" in body) or ("年報" in body)
    return has_login and not has_table_signal


def _peek_latest_period_from_financial_url(url: str, yearCycle: bool = True, retry: int = 2) -> str:
    last_exc = None
    for attempt in range(1, max(1, retry) + 1):
        driver = None
        try:
            driver = _build_driver()
            _driver_get_with_retry(driver, url, retry=2)
            _dismiss_common_popups(driver)
            if _looks_like_login_gate(driver):
                print(f"peek latest period blocked by login: {url}")
                return ""
            root_wait = float(os.getenv("LEGACY_ROOT_WAIT", "18"))
            root_el = _wait_legacy_root_element_with_cache(driver, url, timeout=root_wait, poll=0.4)
            if root_el is not None:
                # Keep precheck aligned with download target:
                # switch to annual mode first, then read latest date.
                try:
                    annual_frame = _legacy_extract_by_root(driver, root_el, yearCycle=yearCycle)
                    _refresh_legacy_root_cache(driver, url, root_el)
                    latest = _latest_period_from_columns([str(c) for c in annual_frame.columns])
                    if latest:
                        return latest
                except Exception:
                    pass
                # Fallback to raw header when frame parse fails.
                date_text = root_el.find_element(By.XPATH, "./section[1]").text.strip()
                headers = [x.strip().replace("(FY)", "") for x in date_text.splitlines() if x.strip()]
                latest = _latest_period_from_columns(headers)
                if latest:
                    return latest

            headers = _headers_snapshot_from_page(driver)
            latest = _latest_period_from_columns(headers)
            if latest:
                return latest
        except Exception as exc:
            last_exc = exc
            print(f"peek latest period failed attempt {attempt}/{retry}: {exc}")
            time.sleep(min(1.2 * attempt, 3))
        finally:
            _safe_quit_driver(driver)
    if last_exc is not None:
        print(f"peek latest period gave up: {last_exc}")
    return ""


def _is_value_like_index(text: str) -> bool:
    s = str(text).strip().replace(",", "")
    if not s or s in {"--", "----", "nan", "None"}:
        return True
    if re.fullmatch(
        r"-?\d+(?:\.\d+)?(?:%|萬億|万亿|億|亿|萬|万|千|百|元|美元|港元|日元|人民幣|人民币|CNY|HKD|USD)?",
        s,
    ):
        return True
    return False


def _sanitize_statement_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cleaned = df.copy()
    cleaned.index = [str(x).strip() for x in cleaned.index]
    drop_mask = []
    for idx in cleaned.index:
        has_text = bool(re.search(r"[A-Za-z\u4e00-\u9fff]", idx))
        bad = (idx.lower().startswith("unnamed:")) or (_is_value_like_index(idx) and not has_text)
        drop_mask.append(bad)
    cleaned = cleaned.loc[[not x for x in drop_mask]]
    cleaned = cleaned[~cleaned.index.duplicated(keep='first')]
    return cleaned


def _enforce_annual_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    work = _clean_period_columns(df)
    annual_cols = _pick_annual_date_columns([str(c) for c in work.columns])
    if annual_cols:
        annual_cols = [c for c in annual_cols if c in work.columns]
        if annual_cols:
            work = work.loc[:, annual_cols]
    return work


def _merge_with_existing(path: Path, new_df: pd.DataFrame, yearCycle: bool = True) -> pd.DataFrame:
    # Keep merge robust even when source tables contain duplicated line items.
    new_df = _sanitize_statement_rows(new_df)
    new_df = _clean_period_columns(new_df)
    if yearCycle:
        new_df = _enforce_annual_columns(new_df)
    if not path.exists():
        return new_df
    try:
        old_df = pd.read_csv(path, index_col=0, encoding='utf-8-sig')
    except Exception:
        return new_df
    old_df = _sanitize_statement_rows(old_df)
    old_df = _clean_period_columns(old_df)
    if yearCycle:
        old_df = _enforce_annual_columns(old_df)

    combined = pd.concat([new_df, old_df], axis=1)
    combined = _clean_period_columns(combined)
    combined = _sanitize_statement_rows(combined)
    if yearCycle:
        combined = _enforce_annual_columns(combined)
    return combined


def _extract_table_via_dom(driver) -> pd.DataFrame:
    table = WebDriverWait(driver, 3).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )
    raw_headers = [
        (th.get_attribute("textContent") or "").strip()
        for th in table.find_elements(By.CSS_SELECTOR, "thead th")
    ]
    if not raw_headers:
        raise RuntimeError("DOM header parse failed")
    # First column is usually row-item title; only drop it when later columns look like periods.
    if len(raw_headers) >= 2 and (not _is_period_like_header(raw_headers[0])) and any(
        _is_period_like_header(x) for x in raw_headers[1:]
    ):
        headers_raw = raw_headers[1:]
    else:
        headers_raw = raw_headers
    headers_raw = [h for h in headers_raw if h]
    if not headers_raw:
        raise RuntimeError("DOM header parse failed")
    rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")

    index_names = []
    values = []
    for row in rows:
        tds = row.find_elements(By.CSS_SELECTOR, "th,td")
        if len(tds) < 2:
            continue
        item_name = (tds[0].get_attribute("title") or tds[0].text or "").strip()
        if not item_name:
            continue
        row_vals = []
        for td in tds[1:]:
            spans = td.find_elements(By.CSS_SELECTOR, "span.value-item")
            if spans:
                raw_val = spans[-1].get_attribute("title") or spans[-1].text
            else:
                raw_val = td.get_attribute("textContent") or td.text
            row_vals.append(_extract_value_only(raw_val))
        if len(row_vals) < len(headers_raw):
            row_vals += ["--"] * (len(headers_raw) - len(row_vals))
        elif len(row_vals) > len(headers_raw):
            row_vals = row_vals[:len(headers_raw)]
        index_names.append(item_name)
        values.append(row_vals)

    if not index_names or not headers_raw:
        raise RuntimeError("DOM table parsing failed")

    return pd.DataFrame(values, index=index_names, columns=headers_raw)


def _merge_dom_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        raise RuntimeError("No DOM frames to merge")
    cleaned = []
    for fr in frames:
        if fr is None or fr.empty:
            continue
        f = fr.copy()
        f.index = [str(x).strip() for x in f.index]
        f = f[~f.index.duplicated(keep='first')]
        f.columns = [str(c).strip() for c in f.columns]
        f = f.loc[:, [bool(c) for c in f.columns]]
        cleaned.append(f)
    if not cleaned:
        raise RuntimeError("All DOM frames empty")
    merged = pd.concat(cleaned, axis=1)
    merged = merged[~merged.index.duplicated(keep='first')]
    return merged


def _extract_table_via_horizontal_scroll(driver, max_steps: int = 16) -> pd.DataFrame:
    """
    Some Futu tables virtualize columns. Scroll horizontally and merge snapshots.
    """
    base = _extract_table_via_dom(driver)
    frames = [base]
    seen = {tuple(str(c) for c in base.columns)}
    try:
        # Move all visible horizontal scrollers together; this works for synchronized virtual tables.
        ratios = np.linspace(0.0, 1.0, num=max(3, max_steps))
        for ratio in ratios:
            _scroll_all_horizontal_containers(driver, float(ratio))
            time.sleep(0.18)
            try:
                fr = _extract_table_via_dom(driver)
                sig = tuple(str(c) for c in fr.columns)
                if sig not in seen:
                    seen.add(sig)
                    frames.append(fr)
            except Exception:
                pass
        # Reset left.
        _reset_horizontal_scroll(driver)
    except Exception:
        pass
    return _merge_dom_frames(frames)


def _frame_is_dom_style(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    if isinstance(df.index, pd.RangeIndex):
        return False
    cols = [str(c).strip() for c in df.columns]
    if not cols:
        return False
    period_like = sum(1 for c in cols if _is_period_like_header(c))
    return period_like >= max(2, int(len(cols) * 0.5))


def _normalize_statement_frame(raw_frame: pd.DataFrame) -> pd.DataFrame:
    if raw_frame is None or raw_frame.empty:
        raise RuntimeError("empty statement frame")
    if _frame_is_dom_style(raw_frame):
        headers_raw = [str(x).strip() for x in raw_frame.columns]
        values_raw = raw_frame.copy()
        index_names = pd.Index([str(x).strip() for x in raw_frame.index])
    else:
        if raw_frame.shape[1] < 2:
            raise RuntimeError("raw statement frame has insufficient columns")
        headers_raw = [str(x).strip() for x in raw_frame.columns[1:]]
        values_raw = raw_frame.iloc[:, 1:].copy()
        index_names = raw_frame.iloc[:, 0].astype(str).str.strip()

    keep_idx = [i for i, h in enumerate(headers_raw) if not _is_yoy_header(h)]
    if keep_idx:
        headers_raw = [headers_raw[i] for i in keep_idx]
        values_raw = values_raw.iloc[:, keep_idx]
    frame = pd.DataFrame(values_raw.to_numpy(), index=index_names, columns=headers_raw)
    frame = frame[frame.index != ""]
    frame = frame[~frame.index.duplicated(keep='first')]
    return frame


def _merge_statement_frames(left: Optional[pd.DataFrame], right: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if left is None or left.empty:
        return right
    if right is None or right.empty:
        return left
    merged = pd.concat([left, right], axis=1)
    merged = _clean_period_columns(merged)
    merged = merged[~merged.index.duplicated(keep='first')]
    return merged


def _validate_legacy_root_xpath(driver, root_xpath: str):
    try:
        el = driver.find_element(By.XPATH, root_xpath)
        if _is_new_financial_reports_root(el):
            return el
        h = el.find_element(By.XPATH, "./section[1]").text.strip()
        b = el.find_element(By.XPATH, "./section[2]").text.strip()
        _ = el.find_element(By.XPATH, "./div[1]")
        if h and b:
            return el
    except Exception:
        return None
    return None


def _element_to_absolute_xpath(driver, element) -> str:
    try:
        return str(
            driver.execute_script(
                """
                function absXPath(el) {
                  if (!el || el.nodeType !== 1) return '';
                  if (el === document.body) return '/html/body';
                  let ix = 1;
                  let sib = el.previousElementSibling;
                  while (sib) {
                    if (sib.tagName === el.tagName) ix++;
                    sib = sib.previousElementSibling;
                  }
                  return absXPath(el.parentElement) + '/' + el.tagName.toLowerCase() + '[' + ix + ']';
                }
                return absXPath(arguments[0]);
                """,
                element,
            )
            or ""
        )
    except Exception:
        return ""


def _locate_legacy_root_element(driver):
    """
    Find financial table root container dynamically, to avoid brittle absolute xpath.
    Expected structure under root:
      ./div[1] (controls), ./section[1] (period headers), ./section[2] (table body text)
    """
    candidates = driver.find_elements(
        By.XPATH,
        "//section[./div and ./section and .//div[contains(.,'年报') or contains(.,'年報') or contains(.,'全部') or contains(.,'Annual') or contains(.,'All')]]",
    )
    for el in candidates:
        try:
            h = el.find_element(By.XPATH, "./section[1]").text.strip()
            b = el.find_element(By.XPATH, "./section[2]").text.strip()
            if h and b:
                return el
        except Exception:
            continue

    new_candidates = driver.find_elements(
        By.XPATH,
        "//section[contains(@class,'reports')][.//div[contains(@class,'filter-nav')] and (.//section[contains(@class,'data-head')] or .//table[contains(@class,'reports-table-none')])]",
    )
    for el in new_candidates:
        if _is_new_financial_reports_root(el):
            return el
    return None


def _get_legacy_root_element_with_cache(driver, url: str):
    key = _cache_key_from_financial_url(url)
    cache = _load_legacy_root_xpath_cache()

    # 1) cached xpath
    cached_xpath = cache.get(key, "").strip()
    if cached_xpath:
        el = _validate_legacy_root_xpath(driver, cached_xpath)
        if el is not None:
            print(f"legacy root cache hit: {cached_xpath}")
            return el
        print(f"legacy root cache invalid: {cached_xpath}")
        cache.pop(key, None)
        _save_legacy_root_xpath_cache()

    # 2) default xpaths
    for xp in LEGACY_ROOT_DEFAULT_XPATHS:
        el = _validate_legacy_root_xpath(driver, xp)
        if el is not None:
            cache[key] = xp
            _save_legacy_root_xpath_cache()
            print(f"legacy root default hit: {xp}")
            return el

    # 3) dynamic locate
    el = _locate_legacy_root_element(driver)
    if el is not None:
        xp = _element_to_absolute_xpath(driver, el)
        if xp:
            cache[key] = xp
            _save_legacy_root_xpath_cache()
            print(f"legacy root dynamic hit: {xp}")
        else:
            print("legacy root dynamic hit (xpath unavailable)")
    return el


def _wait_legacy_root_element_with_cache(driver, url: str, timeout: float = 18.0, poll: float = 0.4):
    deadline = time.time() + max(2.0, float(timeout))
    last_el = None
    while time.time() < deadline:
        try:
            _dismiss_common_popups(driver)
        except Exception:
            pass
        el = _get_legacy_root_element_with_cache(driver, url)
        if el is not None:
            return el
        last_el = el
        time.sleep(max(0.1, float(poll)))
    return last_el


def _refresh_legacy_root_cache(driver, url: str, root_el) -> None:
    """
    Update cached root xpath with the latest valid element path.
    This keeps cache in sync when page layout changes.
    """
    if root_el is None:
        return
    key = _cache_key_from_financial_url(url)
    xp = _element_to_absolute_xpath(driver, root_el).strip()
    if not xp:
        return
    cache = _load_legacy_root_xpath_cache()
    if cache.get(key) != xp:
        cache[key] = xp
        _save_legacy_root_xpath_cache()
        print(f"legacy root cache updated: {xp}")


def _legacy_extract_by_root(driver, root_el, yearCycle: bool = True) -> pd.DataFrame:
    if _is_new_financial_reports_root(root_el):
        return _extract_new_financial_table_by_root(driver, root_el, yearCycle=yearCycle)

    t1 = float(os.getenv("LEGACY_T1", "0.8"))
    t2 = float(os.getenv("LEGACY_T2", "1.2"))

    if yearCycle:
        try:
            _safe_click(driver, root_el.find_element(By.XPATH, "./div[1]/div[1]"))
        except Exception:
            pass
        time.sleep(t1)
        try:
            _safe_click(driver, root_el.find_element(By.XPATH, "./div[1]/div[1]/div/div[2]"))
        except Exception:
            pass
        time.sleep(t1)

    # Keep same behavior as your old script: click these two once.
    try:
        _safe_click(driver, root_el.find_element(By.XPATH, "./div[1]/div[2]"))
    except Exception:
        pass
    time.sleep(t1)
    try:
        _safe_click(driver, root_el.find_element(By.XPATH, "./div[1]/div[3]"))
    except Exception:
        pass
    time.sleep(t2)

    date_text = root_el.find_element(By.XPATH, "./section[1]").text.strip()
    body_text = root_el.find_element(By.XPATH, "./section[2]").text.strip()
    if not date_text or not body_text:
        raise RuntimeError("legacy section text empty")

    columns = [x.strip().replace("(FY)", "") for x in date_text.splitlines() if x.strip()]
    if not columns:
        raise RuntimeError("legacy date columns empty")

    lines = [x.strip() for x in body_text.splitlines() if x.strip()]
    lines = _patch_rows_for_period(lines, len(columns))
    row_len = len(columns) + 1
    row_num = len(lines) // row_len
    if row_num <= 0:
        raise RuntimeError("legacy data rows empty")

    arr = np.array(lines[: row_num * row_len], dtype=object).reshape(row_num, row_len)
    idx = [str(x).strip() for x in arr[:, 0]]
    vals = arr[:, 1:]
    frame = pd.DataFrame(vals, index=idx, columns=columns)
    frame = _sanitize_statement_rows(frame)
    frame = _clean_period_columns(frame)
    if yearCycle:
        frame = _enforce_annual_columns(frame)
    if frame.empty:
        raise RuntimeError("legacy extracted frame empty after clean")
    return frame


def _is_new_financial_reports_root(root_el) -> bool:
    try:
        root_el.find_element(By.XPATH, ".//div[contains(@class,'filter-nav')]")
        root_el.find_element(By.XPATH, ".//table[contains(@class,'reports-table-none')]")
        return True
    except Exception:
        return False


def _extract_new_financial_table_by_root(driver, root_el, yearCycle: bool = True) -> pd.DataFrame:
    t1 = float(os.getenv("LEGACY_T1", "0.8"))
    t2 = float(os.getenv("LEGACY_T2", "1.2"))

    if yearCycle:
        try:
            _safe_click(driver, root_el.find_element(By.XPATH, ".//div[contains(@class,'period-select')]"))
            time.sleep(t1)
            _safe_click(
                driver,
                root_el.find_element(
                    By.XPATH,
                    ".//div[contains(@class,'pouper')]//*[contains(@class,'item')][contains(normalize-space(.), '年報') or contains(normalize-space(.), 'Annual')]",
                ),
            )
            time.sleep(t2)
        except Exception:
            pass

    table = root_el.find_element(By.XPATH, ".//table[contains(@class,'reports-table-none')]")
    header_cells = table.find_elements(By.XPATH, ".//thead//th")
    columns = []
    for th in header_cells[1:]:
        text = (th.text or "").strip().replace("(FY)", "")
        if text:
            columns.append(text)
    if not columns:
        raise RuntimeError("new financial table headers empty")

    rows = table.find_elements(By.XPATH, ".//tbody/tr[contains(@class,'child-item')]")
    data = []
    index_names = []
    for row in rows:
        cells = row.find_elements(By.XPATH, "./td")
        if len(cells) < 2:
            continue
        label = (cells[0].text or "").strip()
        if not label:
            continue
        values = []
        for cell in cells[1:]:
            value_spans = cell.find_elements(
                By.XPATH,
                ".//span[contains(@class,'value-item') and not(contains(@class,'radio-text'))]",
            )
            if value_spans:
                value = (value_spans[-1].text or "").strip()
            else:
                parts = [x.strip() for x in (cell.text or "").splitlines() if x.strip()]
                value = parts[-1] if parts else ""
            values.append(value or "--")
        if len(values) < len(columns):
            values.extend(["--"] * (len(columns) - len(values)))
        index_names.append(label)
        data.append(values[: len(columns)])

    if not data:
        raise RuntimeError("new financial table rows empty")

    frame = pd.DataFrame(data, index=index_names, columns=columns)
    frame = _sanitize_statement_rows(frame)
    frame = _clean_period_columns(frame)
    if yearCycle:
        frame = _enforce_annual_columns(frame)
    if frame.empty:
        raise RuntimeError("new financial table empty after clean")
    return frame


def _extract_financial_table(driver, url: str, yearCycle: bool = True) -> pd.DataFrame:
    print(f"Open financial page: {url}")
    _driver_get_with_retry(driver, url, retry=2)
    t2 = float(os.getenv("LEGACY_T2", "1.2"))
    time.sleep(t2)
    _dismiss_common_popups(driver)
    if _looks_like_login_gate(driver):
        raise RuntimeError(
            "Futu financial page requires login. Please use a logged-in Chrome profile "
            "(set CHROME_USER_DATA_DIR / CHROME_PROFILE_DIR), then retry."
        )

    last_exc = None
    root_wait = float(os.getenv("LEGACY_ROOT_WAIT", "18"))
    try:
        root_el = _wait_legacy_root_element_with_cache(driver, url, timeout=root_wait, poll=0.4)
        if root_el is not None:
            frame = _legacy_extract_by_root(driver, root_el, yearCycle=yearCycle)
            _refresh_legacy_root_cache(driver, url, root_el)
            print(f"legacy root ok: rows={frame.shape[0]} cols={frame.shape[1]}")
            return frame
        print("legacy root not found by dynamic locator")
    except Exception as exc:
        last_exc = exc
        print(f"legacy extract failed: {exc}")

    # Fallback only if legacy extraction fails on both roots.
    merged_frame = None
    use_read_html = os.getenv("ENABLE_READ_HTML_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}
    extractors = [_extract_table_via_section_text, _extract_table_via_dom]
    if use_read_html:
        extractors.append(_extract_table_via_read_html)
    for extractor in extractors:
        try:
            fr = _normalize_statement_frame(extractor(driver))
            print(f"fallback extractor {extractor.__name__}: rows={fr.shape[0]} cols={fr.shape[1]}")
            merged_frame = _merge_statement_frames(merged_frame, fr)
        except Exception as exc:
            if _requires_driver_recreate(exc):
                raise
            print(f"fallback extractor failed: {extractor.__name__}: {exc}")

    if merged_frame is None or merged_frame.empty:
        raise RuntimeError(f"No financial rows parsed from {url}; last legacy error: {last_exc}")

    merged_frame = _sanitize_statement_rows(merged_frame)
    merged_frame = _clean_period_columns(merged_frame)
    if yearCycle:
        merged_frame = _enforce_annual_columns(merged_frame)
    return merged_frame


def downloadFinancialDataFutu(code, yearCycle=True, driver=None):
    finance_root = Path(localPath)
    finance_root.mkdir(parents=True, exist_ok=True)

    income_url = f"https://www.futunn.com/hk/stock/{code[3:]}-{code[0:2]}/financial/income-statement"
    balance_url = f"https://www.futunn.com/hk/stock/{code[3:]}-{code[0:2]}/financial/balance-sheet"
    cash_url = f"https://www.futunn.com/hk/stock/{code[3:]}-{code[0:2]}/financial/cash-flow"

    own_driver = driver is None
    reuse_driver = os.getenv("DOWNLOAD_REUSE_DRIVER", "0").strip().lower() in {"1", "true", "yes", "on"}
    # For server stability, default is NOT reusing sessions across statements.
    if own_driver and reuse_driver:
        driver = _build_driver()

    balancePath = finance_root / f"{code}_balance_year.csv"
    incomePath = finance_root / f"{code}_income_year.csv"
    cashPath = finance_root / f"{code}_cash_year.csv"

    # Fast pre-check: if local income latest date already equals web latest income date, skip this code.
    skip_check = os.getenv("SKIP_IF_LATEST_SAME", "1").strip().lower() in {"1", "true", "yes", "on"}
    if skip_check:
        local_latest = _latest_period_from_local_csv(incomePath)
        if local_latest:
            web_latest = _peek_latest_period_from_financial_url(income_url, yearCycle=yearCycle, retry=2)
            if web_latest and web_latest == local_latest:
                print(f"{code} skip download: income latest date already up-to-date ({local_latest})")
                return
            if web_latest:
                print(f"{code} local/web income latest: {local_latest} / {web_latest}, continue download")
            else:
                print(f"{code} latest-date check unavailable, continue download")

    statement_retry = int(os.getenv("DOWNLOAD_STATEMENT_RETRY", "2"))

    def _download_one(url: str, label: str, retry: int = statement_retry) -> pd.DataFrame:
        nonlocal driver
        last_exc = None
        for attempt in range(1, retry + 1):
            active_driver = None
            created_here = False
            started_at = time.time()
            try:
                print(f"{code} start download {label} attempt {attempt}/{retry}: {url}")
                if reuse_driver:
                    if driver is None:
                        driver = _build_driver()
                    active_driver = driver
                else:
                    active_driver = _build_driver()
                    created_here = True
                frame = _extract_financial_table(active_driver, url, yearCycle=yearCycle)
                print(
                    f"{code} {label} success attempt {attempt}/{retry} "
                    f"in {time.time() - started_at:.1f}s rows={frame.shape[0]} cols={frame.shape[1]}"
                )
                return frame
            except Exception as exc:
                last_exc = exc
                print(
                    f"{code} {label} 下载失败 attempt {attempt}/{retry} "
                    f"after {time.time() - started_at:.1f}s: {exc}"
                )
                if reuse_driver or _requires_driver_recreate(exc):
                    try:
                        if reuse_driver:
                            _safe_quit_driver(driver)
                        elif created_here:
                            _safe_quit_driver(active_driver)
                    except Exception:
                        pass
                    driver = None
                time.sleep(min(1.2 * attempt, 3))
            finally:
                if created_here and active_driver is not None:
                    _safe_quit_driver(active_driver)
        raise RuntimeError(f"{code} {label} download failed after {retry} retries: {last_exc}")
    print(f"{code} financial download started, target root={finance_root}")
    try:
        incomeDataFrame = _download_one(income_url, "income")
        balanceDataFrame = _download_one(balance_url, "balance")
        cashDataFrame = _download_one(cash_url, "cash")
    finally:
        if own_driver and driver is not None:
            _safe_quit_driver(driver)

    def _log_cols(label: str, df: pd.DataFrame) -> None:
        cols = [str(c) for c in df.columns]
        if cols:
            print(f"{code} {label} columns={len(cols)} first={cols[0]} last={cols[-1]}")
        else:
            print(f"{code} {label} columns=0")

    _log_cols("income", incomeDataFrame)
    _log_cols("balance", balanceDataFrame)
    _log_cols("cash", cashDataFrame)

    print('已获取'+code+'财务报表')

    balanceDataFrame = _merge_with_existing(balancePath, balanceDataFrame, yearCycle=yearCycle)
    incomeDataFrame = _merge_with_existing(incomePath, incomeDataFrame, yearCycle=yearCycle)
    cashDataFrame = _merge_with_existing(cashPath, cashDataFrame, yearCycle=yearCycle)

    balanceDataFrame.to_csv(balancePath, mode='w', encoding='utf_8_sig', header=True, index=True)
    incomeDataFrame.to_csv(incomePath, mode='w', encoding='utf_8_sig', header=True, index=True)
    cashDataFrame.to_csv(cashPath, mode='w', encoding='utf_8_sig', header=True, index=True)

def strToNum(ls):   
    num = np.zeros(len(ls))
    #print(ls)
    for i in range(len(ls)):
        j = ls.iloc[i].replace(',', '')
            
        if re.search(r'\d',ls.iloc[i]) == None:
            num[i] = 0        
        elif "萬億" in ls.iloc[i]:
            num[i] = float(re.findall(r'-?\d+(?:\.\d+)?', j)[0])*1000000000000
        elif "億" in ls.iloc[i]:
            num[i] = float(re.findall(r'-?\d+(?:\.\d+)?', j)[0])*100000000
        elif "萬" in ls.iloc[i]:
            num[i] = float(re.findall(r'-?\d+(?:\.\d+)?', j)[0])*10000
        elif "%" in ls.iloc[i]:
            num[i] = float(re.findall(r'-?\d+(?:\.\d+)?', j)[0])*0.01               
        else:
            num[i] = float(j)
    return num


def patch(ls,num):
    index = []
    #print(ls)
    for i in range(len(ls)):
        if (re.search(r'\d',ls[i]) == None) and (re.search('--',ls[i]) == None) :
            index.append(i)
            #print(i)
    index.append(len(ls))
    #print(index)
        
    for j in range(len(index)-1):
        delta = index[j+1]-index[j]
        for k in range(num+1-delta):
            ls.insert(j*(num+1)+delta,'--')
    return ls      

    
def getValue(data, parameters, item):
    ls = parameters.loc[item].dropna()
    #print(ls)
    result = np.zeros(len(data.columns))
    for i in range(len(ls)):
        try:
            num = strToNum(data.loc[ls.iloc[i]])
        except:
            num = np.zeros(len(data.columns)) 
        if i == 0:
            result = num
        else:
            result = result + num
            
    return result
    

def movingAverage(ls):
    Average = ls.copy()
    for i in range(len(ls)-1):
        Average[i] = (ls[i] + ls[i+1])/2
    return Average

def growthRateCalculation(ls):
    Rate = np.zeros(len(ls))
    for i in range(len(ls)-1):
        Rate[i] = (ls[i] - ls[i+1])/ls[i+1]
        if abs(Rate[i]) > 2:
            Rate[i] = 0
    
    return Rate

def CAGR(ls):
    Rate = pow(ls[0]/ls[len(ls)-1], 1/(len(ls)-1)) - 1
    return Rate

def getStockList(fileName):
    f=open(fileName,'rb')
    stock = []
    for line in f.readlines():
        line=line.decode('gbk')
        print(line,end = '')
        line = line.replace('\n','')
        stock.append(line)
    f.close()
    return stock

def getStockInfo(code):
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    
    ret, data = quote_ctx.get_market_snapshot(code)
    if ret == RET_OK:
        print(data)
        print(data['code'][0])    # 取第一条的股票代码
        print(data['code'].values.tolist())   # 转为 list
    else:
        print('error:', data)
    #quote_ctx.close() # 结束后记得关闭当条连接，防止连接条数用尽
    time.sleep(2)
    ret, state = quote_ctx.get_market_state(code)
    if ret == RET_OK:
        print(state)
    else:
        print('error:', state)
    quote_ctx.close()
    return data, state
    
def getStockHistory(code):
    
    historyPath = localPath + code +".csv"
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    ret, data, page_req_key = quote_ctx.request_history_kline(code, start=startDate, end=endDate, max_count=50)  # 每页5个，请求第一页
    if ret == RET_OK:
        data.to_csv(historyPath,mode='w',encoding='utf_8_sig',header=True,index=False)
        print(data)
        print(data['code'][0])    # 取第一条的股票代码
        print(data['close'].values.tolist())   # 第一页收盘价转为 list
    else:
        print('error:', data)
    while page_req_key != None:  # 请求后面的所有结果
        print('*************************************')
        ret, data, page_req_key = quote_ctx.request_history_kline(code, start=startDate, end=endDate, max_count=50, page_req_key=page_req_key) # 请求翻页后的数据
        if ret == RET_OK:
            data.to_csv(historyPath,mode='a',encoding='utf_8_sig',header=False,index=False)
            print(data)
        else:
            print('error:', data)
    print('All pages are finished!')
    quote_ctx.close()  
    
def getStockInfoWeb(code):
    stock_url = f"https://www.futunn.com/hk/stock/{code[3:]}-{code[0:2]}"
    print(stock_url)
    fast_info = _fetch_stock_info_via_http(stock_url, code)
    if fast_info is not None:
        _write_stock_info_csv(code, *fast_info)
        return

    body = ""
    detail_map: dict[str, str] = {}
    last_exc = None
    for attempt in range(1, 4):
        driver = _build_driver()
        try:
            driver.get(stock_url)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(1.5)
            _dismiss_common_popups(driver)

            try:
                expand_btn = WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located(
                        (
                            By.XPATH,
                            "//div[contains(@class,'detail-card')]//*[contains(@class,'right-tip') or contains(@class,'arrow-double-down')]",
                        )
                    )
                )
                _safe_click(driver, expand_btn)
                time.sleep(1.2)
            except Exception:
                pass

            body = driver.find_element(By.TAG_NAME, "body").text
            detail_map = _extract_stock_detail_map(driver)
            if body:
                break
        except (TimeoutException, WebDriverException) as exc:
            last_exc = exc
            print(f"{code} info page failed attempt {attempt}/3: {exc}")
            time.sleep(min(2 * attempt, 5))
        finally:
            _safe_quit_driver(driver)

    if not body:
        raise RuntimeError(f"get stock info failed for {code}: {last_exc}")

    lines = [x.strip() for x in body.splitlines() if x.strip()]
    code_part = code[3:]

    name = code
    price = "--"
    market_value = "--"
    total_shares = "--"
    pe = "--"
    pb = "--"
    dy = "--"

    for i, line in enumerate(lines):
        if f"({code_part})" in line and "股價" not in line:
            name = line.split("(")[0].strip()
            for j in range(i + 1, min(i + 8, len(lines))):
                if re.fullmatch(r"-?\d+(?:\.\d+)?", lines[j]):
                    price = lines[j]
                    break
            break

    def _value_near(labels, default: str = "--") -> str:
        if isinstance(labels, str):
            labels = [labels]
        for idx, line in enumerate(lines):
            if not any(lb in line for lb in labels):
                continue
            # 1) Same line number extraction.
            m = re.search(r"-?\d+(?:\.\d+)?(?:%|萬億|万亿|億|亿|萬|万)?", line.replace(",", ""))
            if m:
                return m.group(0)
            # 2) Previous line.
            if idx > 0:
                prev = lines[idx - 1]
                if re.search(r"\d", prev):
                    return prev
            # 3) Next line.
            if idx + 1 < len(lines):
                nxt = lines[idx + 1]
                if re.search(r"\d", nxt):
                    return nxt
        return default

    market_value = (
        detail_map.get("總市值")
        or detail_map.get("总市值")
        or _value_near(["總市值", "总市值"], market_value)
    )
    pe = (
        detail_map.get("市盈率TTM")
        or detail_map.get("市盈率(靜)")
        or detail_map.get("市盈率")
        or _value_near(["市盈率TTM", "市盈率"], pe)
    )
    pb = (
        detail_map.get("市淨率")
        or detail_map.get("市净率")
        or detail_map.get("PB")
        or _value_near(["市淨率", "市净率", "PB"], pb)
    )
    total_shares = (
        detail_map.get("總股本")
        or detail_map.get("总股本")
        or _value_near(["總股本", "总股本"], total_shares)
    )
    dy = (
        detail_map.get("股息率TTM")
        or detail_map.get("股息率LFY")
        or _value_near(["股息率TTM", "股息率LFY", "股息率", "股息"], dy)
    )

    _write_stock_info_csv(code, name, price, market_value, total_shares, pe, pb, dy)


def _manual_login_once(code: str) -> None:
    """Open a visible browser once so the user can complete Futu login."""

    original_headless = os.getenv("CHROME_HEADLESS_MODE", "new")
    original_interactive = os.getenv("FUTU_INTERACTIVE_LOGIN", "")
    os.environ["CHROME_HEADLESS_MODE"] = "off"
    os.environ["FUTU_INTERACTIVE_LOGIN"] = "1"
    driver = None
    try:
        driver = _build_driver()
        home_url = "https://www.futunn.com/"
        print(f"Open visible browser for manual login: {home_url}")
        driver.get(home_url)
        time.sleep(2.5)
        print(
            "\n请在弹出的 Chrome 中手动完成 Futu 登录。"
            f"\n登录态将保存在: {os.getenv('CHROME_USER_DATA_DIR', str(DEFAULT_FUTU_PROFILE_DIR))}"
            "\n这一步只负责登录，不会继续加载财务页。"
            "\n如果页面还没完全出来，请先等待 5-10 秒再操作。"
            "\n登录完成后回到终端按回车，后续下载再自动走 headless ..."
        )
        input()
        _mark_login_session_ready()
    finally:
        _safe_quit_driver(driver)
        os.environ["CHROME_HEADLESS_MODE"] = original_headless
        if original_interactive:
            os.environ["FUTU_INTERACTIVE_LOGIN"] = original_interactive
        else:
            os.environ.pop("FUTU_INTERACTIVE_LOGIN", None)


def _mark_login_session_ready() -> None:
    DEFAULT_FUTU_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "profile_dir": os.getenv("CHROME_USER_DATA_DIR", str(DEFAULT_FUTU_PROFILE_DIR)),
        "mode": "manual_login_then_headless",
    }
    FUTU_LOGIN_STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_stock_detail_map(driver) -> dict[str, str]:
    detail_map: dict[str, str] = {}
    try:
        cards = driver.find_elements(
            By.XPATH,
            "//div[contains(@class,'detail-card')]//*[contains(@class,'card-item')]",
        )
    except Exception:
        return detail_map

    for card in cards:
        try:
            spans = card.find_elements(By.XPATH, "./span")
            if len(spans) < 2:
                continue
            value = (spans[0].text or "").strip()
            label = (spans[1].text or "").strip()
            if label and value and label not in detail_map:
                detail_map[label] = value
        except Exception:
            continue
    return detail_map


def _extract_stock_detail_map_from_html(text: str) -> dict[str, str]:
    detail_map: dict[str, str] = {}
    try:
        root = etree.HTML(text)
    except Exception:
        return detail_map
    if root is None:
        return detail_map

    nodes = root.xpath("//div[contains(@class,'detail-card')]//*[contains(@class,'card-item')]")
    for node in nodes:
        spans = node.xpath("./span")
        if len(spans) < 2:
            continue
        value = "".join(spans[0].itertext()).strip()
        label = "".join(spans[1].itertext()).strip()
        if label and value and label not in detail_map:
            detail_map[label] = value
    return detail_map


def _fetch_stock_info_via_http(stock_url: str, code: str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(stock_url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        print(f"{code} http stock info fetch failed: {exc}")
        return None

    text = resp.text
    detail_map = _extract_stock_detail_map_from_html(text)

    title = ""
    m = re.search(r"<title>\s*([^<(]+)\(([^)]+)\)", text, flags=re.I)
    if m:
        title = m.group(1).strip()

    def _pick(*labels, default="--"):
        for label in labels:
            value = detail_map.get(label)
            if value:
                return value
        return default

    name = title or code
    price = _pick("平均價", "平均价")
    market_value = _pick("總市值", "总市值")
    total_shares = _pick("總股本", "总股本")
    pe = _pick("市盈率TTM", "市盈率(靜)", "市盈率")
    pb = _pick("市淨率", "市净率", "PB")
    dy = _pick("股息率TTM", "股息率LFY", "股息率")

    # Fast path is only considered valid if the key detail fields are present.
    if total_shares == "--" and pb == "--" and dy == "--":
        return None
    return name, price, market_value, total_shares, pe, pb, dy


def _write_stock_info_csv(code: str, name: str, price: str, market_value: str, total_shares: str, pe: str, pb: str, dy: str) -> None:
    print(name)
    print('已获取'+code+'信息')
    stockInfo = pd.DataFrame(
        {"Stock": [code, name, price, market_value, total_shares, pe, pb, dy]},
        index=["股票代码", "股票名称", "平均价", "总市值", "总股本", "市盈率", "市净率", "股息率"],
    )
    stockInfoPath = Path(localPath) / f"{code}_info.csv"
    stockInfo.to_csv(stockInfoPath, mode='w', encoding='utf_8_sig', header=False, index=True)


def normalize_code(raw_code: str) -> str:
    text = raw_code.strip().upper()
    if not text:
        return text
    if "." in text:
        return text

    if re.fullmatch(r"\d{6}", text):
        if text.startswith(("6", "9")):
            return f"SH.{text}"
        if text.startswith(("0", "3")):
            return f"SZ.{text}"
    if re.fullmatch(r"\d{5}", text):
        return f"HK.{text}"
    if re.fullmatch(r"\d{4}", text):
        return f"HK.{text.zfill(5)}"
    return f"US.{text}"


def parse_args():
    parser = argparse.ArgumentParser(description="Download stock info and financial reports from Futu")
    parser.add_argument(
        "code",
        help="Stock code, e.g. SH.600519 / HK.00700 / US.AAPL / 600519 / 00700 / AAPL",
    )
    parser.add_argument(
        "--skip-info",
        action="store_true",
        help="Skip downloading stock snapshot info csv",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Download full table columns (not only FY columns)",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Disable headless mode to show Chrome window for debugging",
    )
    parser.add_argument(
        "--use-local-driver",
        action="store_true",
        help="Prefer the locally installed chromedriver instead of forcing Selenium Manager",
    )
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="Open a visible browser once so you can manually log into Futu and persist the session",
    )
    return parser.parse_args()


def main():
    _ensure_runtime_env_defaults()
    args = parse_args()
    if args.show_browser and not args.manual_login:
        os.environ["CHROME_HEADLESS_MODE"] = "off"
    if args.use_local_driver:
        os.environ["USE_LOCAL_CHROMEDRIVER"] = "1"
        os.environ.pop("DISABLE_LOCAL_CHROMEDRIVER", None)
    code = normalize_code(args.code)
    print(f"Start download for {code}")
    if args.manual_login:
        _manual_login_once(code)
        print(f"Login session saved for {code}. Next runs will reuse the profile in headless mode.")
        return
    if not args.skip_info:
        try:
            getStockInfoWeb(code)
        except Exception as exc:
            print(f"Warning: getStockInfoWeb failed for {code}, continue download: {exc}")
    downloadFinancialDataFutu(code, yearCycle=(not args.full))
    print(f"Download completed for {code}")


if __name__ == "__main__":
    main()

    
    
    
