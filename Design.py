import os
import re
import json
import logging
import requests
import asyncio
from io import BytesIO
from playwright.async_api import async_playwright
from telegram import Update
from telegram.error import Conflict
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── API Keys ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN",  "7804696325:AAF_F5Hxxq0k8Nxn_B_3Zku-2_DwodWMiMk")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "55249815-235a36a1b0c8c91f37339ed87")
FREEPIK_API_KEY = os.environ.get("FREEPIK_API_KEY", "FPSX46d9ab6c4f49fd968e4d0a38ba4c362d")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY",    "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")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

WELCOME_MESSAGE = (
    "🚀 *Advanced Design Bot*\n\n"
    "SVG assets khuje debo Hugeicons, FlatIcon, unDraw \\& Pixabay theke\\!\n\n"
    "📂 *Commands:*\n"
    "• `/icon <n>` — Hugeicons icon\n"
    "• `/illustration <n>` — unDraw illustration\n"
    "• `/image <n>` — Pixabay vector\n"
    "• `/topic <topic>` — Groq AI recommendation\n\n"
    "🔗 *Link paste koro:*\n"
    "HugeIcons ba FlatIcon link dile direct SVG debo\\!"
)


# ─── Helpers ──────────────────────────────────────────────────────────────────
def escape_md(text: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+=|{}.!\\-])", r"\\\1", text)


def format_svg(svg: str) -> str:
    svg = re.sub(r"\s+", " ", svg)
    svg = svg.replace("> <", ">\n  <")
    return svg.strip()


def detect_platform(url: str) -> str | None:
    if "hugeicons.com" in url:  return "hugeicons"
    if "flaticon.com"  in url:  return "flaticon"
    if "undraw.co"     in url:  return "undraw"
    if "freepik.com"   in url:  return "freepik"
    return None


# ─── SVG Fetchers ─────────────────────────────────────────────────────────────
def fetch_hugeicons_svg(url: str) -> dict:
    """CDN-first fetch, falls back to page scrape."""
    match = re.search(r"hugeicons\.com/icon/([^?#/]+)", url)
    if not match:
        raise ValueError("Invalid HugeIcons URL — expected /icon/<name>")

    icon_name = match.group(1)
    style_match = re.search(r"[?&]style=([^&]+)", url)
    style = style_match.group(1) if style_match else "stroke-rounded"

    # 1️⃣ CDN direct
    cdn_url = f"https://cdn.hugeicons.com/icons/{icon_name}-{style}.svg?v=1.0.0"
    try:
        res = requests.get(cdn_url, headers={**HEADERS, "Referer": "https://hugeicons.com/"}, timeout=10)
        if res.status_code == 200 and "<svg" in res.text:
            return {"svg": res.text.strip(), "icon_name": icon_name, "style": style, "source": "HugeIcons"}
    except Exception as e:
        logger.warning(f"HugeIcons CDN miss: {e}")

    # 2️⃣ Page scrape fallback
    page_res = requests.get(url, headers=HEADERS, timeout=10)
    svg_match = re.search(r"<svg[\s\S]*?</svg>", page_res.text, re.IGNORECASE)
    if svg_match:
        return {"svg": svg_match.group(0), "icon_name": icon_name, "style": style, "source": "HugeIcons"}

    raise ValueError("SVG not found on HugeIcons")


def fetch_flaticon_svg(url: str) -> dict:
    res = requests.get(url, headers=HEADERS, timeout=10)
    if res.status_code != 200:
        raise ValueError(f"FlatIcon returned {res.status_code}")

    # Strategy 1: inline <svg>
    m = re.search(r"<svg[\s\S]*?</svg>", res.text, re.IGNORECASE)
    if m:
        return {"svg": m.group(0), "icon_name": "flaticon-icon", "style": "default", "source": "FlatIcon"}

    # Strategy 2: JSON-embedded
    m = re.search(r'"svg"\s*:\s*"([^"]+)"', res.text)
    if m:
        svg = m.group(1).replace("\\n", "\n").replace('\\"', '"').replace("\\/", "/")
        return {"svg": svg, "icon_name": "flaticon-icon", "style": "default", "source": "FlatIcon"}

    # Strategy 3: CDN link
    m = re.search(r"https://[^\s\"']+\.svg", res.text)
    if m:
        svg_res = requests.get(m.group(0), headers=HEADERS, timeout=10)
        if "<svg" in svg_res.text:
            return {"svg": svg_res.text.strip(), "icon_name": "flaticon-icon", "style": "default", "source": "FlatIcon"}

    raise ValueError("SVG not found on FlatIcon")


# ─── unDraw (needs Playwright — JS-rendered app) ──────────────────────────────
async def fetch_undraw_svg(query: str) -> tuple[str, str | None]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(
                f"https://undraw.co/search/{query.replace(' ', '%20')}",
                timeout=30000, wait_until="networkidle",
            )
            await page.wait_for_selector("button", timeout=8000)
            skip = {"undraw", "supporters", "more", "illustrations", "load more", "search"}
            for btn in await page.query_selector_all("button"):
                label = (await btn.inner_text()).strip()
                if label and len(label) > 2 and label.lower() not in skip:
                    await btn.click()
                    await page.wait_for_selector("svg.injected-svg", timeout=5000)
                    svg = await page.evaluate(
                        '() => document.querySelector("svg.injected-svg")?.outerHTML'
                    )
                    return label, svg
        except Exception as e:
            logger.error(f"unDraw error: {e}")
        finally:
            await browser.close()
    return query, None


# ─── Groq AI ──────────────────────────────────────────────────────────────────
async def get_groq_recommendations(topic: str) -> dict:
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Designer needs assets for topic: '{topic}'.\n"
                        "Return ONLY JSON (no markdown):\n"
                        '{"illustration":"keyword","icons":["icon1","icon2"],"vector":"keyword"}'
                    ),
                }],
                "response_format": {"type": "json_object"},
            },
            timeout=15,
        )
        return json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return {"illustration": topic, "icons": [topic, topic], "vector": topic}


# ─── Send SVG helper ──────────────────────────────────────────────────────────
async def send_svg_result(update: Update, result: dict):
    icon_name = result.get("icon_name", "icon")
    style     = result.get("style", result.get("source", "icon"))
    clean_svg = format_svg(result["svg"])
    file_name = f"{icon_name}-{style}.svg"

    await update.message.reply_text(
        f"✅ *{escape_md(icon_name)}* \\({escape_md(style)}\\)",
        parse_mode="MarkdownV2",
    )

    preview = clean_svg if len(clean_svg) <= 3800 else clean_svg[:3800] + "\n<!-- truncated -->"
    await update.message.reply_text(f"```xml\n{preview}\n```", parse_mode="MarkdownV2")

    file_bytes = BytesIO(clean_svg.encode("utf-8"))
    file_bytes.name = file_name
    await update.message.reply_document(
        document=file_bytes,
        filename=file_name,
        caption=f"📁 {file_name} — download kore direct use koro!",
    )


# ─── Error handler ────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        logger.warning("409 Conflict — kill the other bot instance first. Stopping.")
        asyncio.get_event_loop().stop()
    else:
        logger.error(f"Unhandled error: {context.error}", exc_info=context.error)


# ─── Handlers ────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MESSAGE, parse_mode="MarkdownV2")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Primary UX: user pastes a HugeIcons or FlatIcon link."""
    text = update.message.text or ""
    url_match = re.search(r"https?://[^\s]+", text)

    if not url_match:
        await update.message.reply_text(
            "⚠️ Ekta valid link dao\\! HugeIcons ba FlatIcon link paste koro\\.",
            parse_mode="MarkdownV2",
        )
        return

    url = url_match.group(0)
    platform = detect_platform(url)

    if platform not in ("hugeicons", "flaticon"):
        await update.message.reply_text(
            "❌ Sudhu HugeIcons ba FlatIcon link support kori ekhane\\.\n"
            "unDraw er jonno `/illustration` command use koro\\.",
            parse_mode="MarkdownV2",
        )
        return

    status = await update.message.reply_text("⏳ Fetch kortesi\\.\\.\\.", parse_mode="MarkdownV2")
    try:
        result = fetch_hugeicons_svg(url) if platform == "hugeicons" else fetch_flaticon_svg(url)
        await status.delete()
        await send_svg_result(update, result)
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        await status.delete()
        await update.message.reply_text(
            "❌ Fetch korte parlam na\\!\n\n"
            "• Link valid na\n• Site block kortese\n• Icon exist kore na\n\n"
            "Arekbar try koro ba onyo link dao\\.",
            parse_mode="MarkdownV2",
        )


async def handle_icon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        return await update.message.reply_text(
            "Icon naam bolun\\! e\\.g\\. `/icon home-01`", parse_mode="MarkdownV2"
        )
    slug = query.lower().replace(" ", "-")
    status = await update.message.reply_text(
        f"🔍 HugeIcons CDN-e `{escape_md(slug)}` khujchi\\.\\.\\.", parse_mode="MarkdownV2"
    )
    try:
        result = fetch_hugeicons_svg(f"https://hugeicons.com/icon/{slug}?style=stroke-rounded")
        await status.delete()
        await send_svg_result(update, result)
    except Exception:
        await status.delete()
        await update.message.reply_text(
            f"❌ `{escape_md(slug)}` pawa jayni\\.\n"
            f"Browse koro: [hugeicons\\.com/icons](https://hugeicons.com/icons?search={slug})",
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
        )


async def handle_illu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        return await update.message.reply_text(
            "Illustration naam bolun\\! e\\.g\\. `/illustration team work`", parse_mode="MarkdownV2"
        )
    status = await update.message.reply_text(
        f"🔍 unDraw-e `{escape_md(query)}` khujchi\\.\\.\\.", parse_mode="MarkdownV2"
    )
    label, svg = await fetch_undraw_svg(query)
    await status.delete()
    if svg:
        await send_svg_result(update, {"svg": svg, "icon_name": label, "style": "unDraw", "source": "unDraw"})
    else:
        await update.message.reply_text(
            f"❌ `{escape_md(query)}` unDraw\\-e pawa jayni\\.", parse_mode="MarkdownV2"
        )


async def handle_image_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        return await update.message.reply_text(
            "Keyword bolun\\! e\\.g\\. `/image business`", parse_mode="MarkdownV2"
        )
    status = await update.message.reply_text(
        f"🔍 Pixabay-e `{escape_md(query)}` khujchi\\.\\.\\.", parse_mode="MarkdownV2"
    )
    try:
        resp = requests.get(
            "https://pixabay.com/api/vectors/",
            params={"key": PIXABAY_API_KEY, "q": query, "per_page": 3, "safesearch": "true"},
            timeout=10,
        )
        hits = resp.json().get("hits", [])
        await status.delete()
        if not hits:
            await update.message.reply_text(
                f"❌ `{escape_md(query)}` Pixabay\\-e pawa jayni\\.", parse_mode="MarkdownV2"
            )
            return
        for hit in hits:
            await update.message.reply_photo(
                photo=hit["webformatURL"],
                caption=f"🖼 {hit.get('tags', query)}\n🔗 {hit['pageURL']}",
            )
    except Exception as e:
        logger.error(f"Pixabay error: {e}")
        await status.delete()
        await update.message.reply_text(
            "❌ Pixabay theke data aante somosya hoyeche\\.", parse_mode="MarkdownV2"
        )


async def handle_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text(
            "Topic bolun\\! e\\.g\\. `/topic healthcare`", parse_mode="MarkdownV2"
        )
    status = await update.message.reply_text(
        f"🤖 Groq AI diye `{escape_md(topic)}` analyze korchi\\.\\.\\.", parse_mode="MarkdownV2"
    )
    recs = await get_groq_recommendations(topic)
    icons = recs.get("icons", [topic, topic])
    await status.delete()
    await update.message.reply_text(
        f"🎨 *Groq AI — {escape_md(topic)}*\n\n"
        f"🖼 `/illustration {escape_md(recs.get('illustration', topic))}`\n"
        f"🔷 `/icon {escape_md(icons[0])}`  •  `/icon {escape_md(icons[1] if len(icons) > 1 else icons[0])}`\n"
        f"📐 `/image {escape_md(recs.get('vector', topic))}`",
        parse_mode="MarkdownV2",
    )


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("icon",         handle_icon_cmd))
    app.add_handler(CommandHandler("illustration", handle_illu_cmd))
    app.add_handler(CommandHandler("image",        handle_image_cmd))
    app.add_handler(CommandHandler("topic",        handle_topic_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Advanced Design Bot is running…")
    app.run_polling(drop_pending_updates=True, close_loop=False)