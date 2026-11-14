# 📦 Builds and releases / ساخت و انتشار

**CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 · نسخه / version `1.0.0`**

---

## 🔢 Versioning / نسخه‌گذاری

<div dir="rtl">

نسخه در یک‌جا تعریف می‌شود: `__version__` در `src/cyrusasha_odl/__init__.py`. نام نمایشی محصول همیشه به شکل `CyrusAsha_GuiForOpenDataLoaderPDF-<نسخه>` است و در عنوان README، عنوان پنجره‌ی launcherها، میان‌بر دسکتاپ و نام فایل انتشار تکرار می‌شود. آزمون `test/test_launchers.py` یکسان بودن نسخه در README و launcherها را بررسی می‌کند.

</div>

The version is defined in one place: `__version__` in `src/cyrusasha_odl/__init__.py`. The product’s display name is always `CyrusAsha_GuiForOpenDataLoaderPDF-<version>` and appears in the README title, the launcher window titles, the desktop shortcut and the release file name. `test/test_launchers.py` checks that the README and the launchers carry the same version.

<div dir="rtl">

برای نسخه‌ی تازه، این موارد را به‌روز کنید: `__init__.py`، عنوان و خط «نسخه / Version» در `README.md`، سرخط `docs/*.md`، عنوان‌های `windows/*.bat` و `windows/install-shortcut.ps1` و ثابت `BRAND.version` در `src/gui/public/js/app.js`.

</div>

For a new version, update: `__init__.py`, the title and the “نسخه / Version” line of `README.md`, the headers of `docs/*.md`, the titles in `windows/*.bat` and `windows/install-shortcut.ps1`, and the `BRAND.version` constant in `src/gui/public/js/app.js`.

---

## 🎨 Brand assets / دارایی‌های برند

<div dir="rtl">

همه‌ی تصاویر برند از یک فایل ساخته می‌شوند: `assets/CyrusAsha_GuiForOpenDataLoaderPDF-logo.png` (لوگوی شفاف CyrusAsha). اسکریپت زیر نشان هدر، favicon، لوگوی کامل صفحه‌ی راهنما و آیکون ویندوز با ده اندازه (۱۶ تا ۲۵۶ پیکسل) را تولید می‌کند. این اسکریپت ابزار توسعه است و به Pillow نیاز دارد؛ خروجی‌ها در مخزن ثبت می‌شوند و کاربران به Pillow نیاز ندارند.

</div>

All brand images are generated from one file: `assets/CyrusAsha_GuiForOpenDataLoaderPDF-logo.png` (the transparent CyrusAsha logo). The script below produces the header emblem, the favicon, the full logo for the Help page and the Windows icon with ten sizes (16–256 px). It is a developer tool and needs Pillow; the outputs are committed, so users never need Pillow.

```bash
pip install pillow
python scripts/build-icons.py
```

---

## 🧪 Tests / آزمون‌ها

<div dir="rtl">

پیش از هر انتشار همه‌ی آزمون‌ها را اجرا کنید. آزمون‌های اصلی فقط به کتابخانه‌ی استاندارد پایتون نیاز دارند؛ آزمون رابط گرافیکی اختیاری است و هر صفحه را به دو زبان باز و خطاهای جاوااسکریپت را بررسی می‌کند.

</div>

Run all tests before every release. The main suites need only the Python standard library; the GUI test is optional and opens every page in both languages while checking for JavaScript errors.

```bash
python -m unittest discover -s test -v

# optional: end-to-end RTL test with the real CLI (Python of the private venv)
ODL_TEST_PYTHON=data/venv/bin/python python -m unittest test.test_rtl -v   # Windows: data\venv\Scripts\python.exe

pip install playwright
python -m playwright install chromium
python -m unittest test.test_gui -v
```

<div dir="rtl">

| فایل آزمون | محتوا |
|---|---|
| `test/test_backend.py` | تنظیمات، قوانین، امنیت مسیرها، آرگومان‌های CLI، تقسیم دسته‌ها، zip slip و آزمون واقعی API |
| `test/test_i18n.py` | کامل بودن متن‌های فارسی و انگلیسی، برچسب همه‌ی گزینه‌ها و پیام همه‌ی کدهای خطا |
| `test/test_launchers.py` | ASCII و CRLF بودن launcherها، مسیرهای ارجاع‌شده، آیکون ده‌اندازه‌ای، README دوزبانه و یکسانی نسخه |
| `test/test_rtl.py` | اصلاح خروجی راست‌به‌چپ روی خروجی‌های واقعی opendataloader-pdf (`test/fixtures/rtl`)، لیگاتورها، جدول‌ها و مرحله‌ی کار؛ آزمون سرتاسری اختیاری با `ODL_TEST_PYTHON` |
| `test/test_storage.py` | پوشه‌ی نصب و مدل‌ها: مسیرها، تنظیم‌ها، متغیرهای محیطی، بررسی پوشه، انتقال (تغییر نام، کپی با پیشرفت، لغو، پیوندها)، عملیات `relocate` با بازگردانی، API |
| `test/test_gui.py` | باز کردن همه‌ی صفحه‌ها به دو زبان با Playwright (اختیاری) |

</div>

| Test file | Coverage |
|---|---|
| `test/test_backend.py` | Settings, rules, path safety, CLI arguments, batching, zip slip and a real API smoke test |
| `test/test_i18n.py` | Complete Persian and English texts, labels for every option and messages for every error code |
| `test/test_launchers.py` | ASCII/CRLF launchers, referenced paths, ten-size icon, bilingual README and version consistency |
| `test/test_rtl.py` | RTL repair on real opendataloader-pdf outputs (`test/fixtures/rtl`), ligatures, tables and the job step; optional end-to-end test with `ODL_TEST_PYTHON` |
| `test/test_storage.py` | Install/models folders: paths, settings, environment variables, folder checks, moving (rename, copy with progress, cancel, links), the `relocate` action with rollback, API |
| `test/test_gui.py` | Opens every page in both languages with Playwright (optional) |

---

## 🗜️ Release package / بسته‌ی انتشار

<div dir="rtl">

اسکریپت زیر فقط با کتابخانه‌ی استاندارد پایتون، فایل `release/CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0.zip` را با یک پوشه‌ی سطح بالا به همین نام می‌سازد و checksum آن را در فایل `.sha256` می‌نویسد. پوشه‌های `data/` و `release/` و فایل‌های موقت پایتون در بسته قرار نمی‌گیرند و اگر یک launcher پایان خط CRLF یا ASCII بودن را از دست داده باشد، ساخت متوقف می‌شود.

</div>

The script below — standard library only — creates `release/CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0.zip` with a single top-level folder of the same name and writes its checksum to a `.sha256` file. The `data/` and `release/` folders and Python cache files are excluded, and the build stops if a launcher has lost its CRLF line endings or contains non-ASCII bytes.

```bash
python scripts/build-release.py
```

---

## 🚀 Publishing on GitHub / انتشار در GitHub

<div dir="rtl">

۱. در GitHub یک مخزن خالی به نام `CyrusAsha_GuiForOpenDataLoaderPDF` بسازید (بدون README، چون پروژه README دارد).

۲. در پوشه‌ی پروژه، مخزن محلی را بسازید و اولین commit را ارسال کنید (دستورهای زیر). فایل `.gitattributes` پایان خط فایل‌های `.bat` را CRLF نگه می‌دارد.

۳. برچسب نسخه (`1.0.0`) را بسازید و ارسال کنید.

۴. در بخش **Releases** یک انتشار تازه بر اساس همان برچسب بسازید، عنوان را `CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0` بگذارید و فایل ZIP و `.sha256` پوشه‌ی `release/` را پیوست کنید.

۵. در بخش **About** مخزن، توضیح کوتاه و موضوع‌هایی مثل `pdf`، `opendataloader`، `rag`، `persian`، `rtl` و `windows` را اضافه کنید.

</div>

1. Create an empty repository named `CyrusAsha_GuiForOpenDataLoaderPDF` on GitHub (without a README — the project already has one).
2. Initialise the local repository in the project folder and push the first commit (commands below). `.gitattributes` keeps CRLF line endings for `.bat` files.
3. Create and push the version tag (`1.0.0`).
4. Under **Releases**, draft a new release from that tag, title it `CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0` and attach the ZIP and `.sha256` files from `release/`.
5. In the repository’s **About** box, add a short description and topics such as `pdf`, `opendataloader`, `rag`, `persian`, `rtl` and `windows`.

```bash
git init -b main
git add .
git commit -m "Initial commit: CyrusAsha_GuiForOpenDataLoaderPDF 1.0.0"
git remote add origin https://github.com/hamzeh-asadi/CyrusAsha_GuiForOpenDataLoaderPDF.git
git push -u origin main

git tag -a 1.0.0 -m "CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0"
git push origin 1.0.0
```

<div dir="rtl">

پیشنهاد متن «About»:

</div>

Suggested “About” text:

```text
CyrusAsha control center for OpenDataLoader PDF: one-click install, drag & drop PDF → Markdown/JSON/HTML, hybrid AI server, Persian/English UI. Local processing; no dependencies.
```

---

## ✅ Release checklist / چک‌لیست انتشار

<div dir="rtl">

- [ ] نسخه در همه‌ی جاهای فهرست‌شده‌ی بخش «نسخه‌گذاری» به‌روز شده است.
- [ ] `python -m unittest discover -s test -v` بدون خطا اجرا می‌شود (و در صورت امکان آزمون GUI).
- [ ] تصاویر `docs/screenshots` با رابط فعلی هم‌خوان هستند.
- [ ] فایل `src/cyrusasha_odl/data/rtl-words.txt.gz` در بسته هست (اگر منابع فهرست واژه تغییر کرده، `scripts/build-rtl-lexicon.py` دوباره اجرا شده است).
- [ ] `python scripts/build-release.py` اجرا و ZIP روی یک ویندوز تمیز آزمایش شده است: `Start.bat` ← «نصب همه‌چیز» ← تبدیل یک PDF.
- [ ] ZIP و `.sha256` به GitHub Release پیوست شده‌اند.

</div>

- [ ] The version is updated everywhere listed under “Versioning”.
- [ ] `python -m unittest discover -s test -v` passes (and the GUI test if possible).
- [ ] The images in `docs/screenshots` match the current UI.
- [ ] `src/cyrusasha_odl/data/rtl-words.txt.gz` is part of the package (if the word-list sources changed, `scripts/build-rtl-lexicon.py` was run again).
- [ ] `python scripts/build-release.py` was run and the ZIP was tested on a clean Windows machine: `Start.bat` → “Install everything” → convert one PDF.
- [ ] The ZIP and `.sha256` files are attached to the GitHub Release.
