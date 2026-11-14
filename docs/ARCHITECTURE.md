# 🏛️ Architecture / معماری

**CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 · نسخه / version `1.0.0`**

---

## 🗺️ Overview / تصویر کلی

<div dir="rtl">

برنامه از دو بخش ساخته شده است: یک **سرور HTTP محلی** به زبان پایتون (فقط کتابخانه‌ی استاندارد) و یک **رابط وب** با HTML/CSS/JavaScript خالص. سرور، opendataloader-pdf را از یک محیط مجازی اختصاصی اجرا می‌کند، سرور هیبرید را مدیریت می‌کند و نصب‌کننده را اجرا می‌کند؛ رابط وب فقط از طریق یک API از نوع JSON با سرور حرف می‌زند.

</div>

The program consists of two parts: a **local HTTP server** written in Python (standard library only) and a **web UI** in plain HTML/CSS/JavaScript. The server runs opendataloader-pdf from a private virtual environment, manages the hybrid server and runs the installer; the web UI talks to the server exclusively through a JSON API.

```text
Browser (Edge --app window)  ──HTTP/JSON──►  src/main.py → cyrusasha_odl.server (ThreadingHTTPServer)
                                                 │
                                                 ├─ JobManager ───► python -m opendataloader_pdf … ──► java -jar (JVM)
                                                 ├─ HybridManager ► python -m opendataloader_pdf.hybrid_server (docling)
                                                 ├─ SetupManager ─► venv · pip · Adoptium (portable Java) · winget
                                                 ├─ EnvInspector ─► java -version · venv metadata · PyPI JSON API
                                                 └─ RuleStore / PresetStore / Settings ─► data/*.json
```

---

## 🧩 Modules / ماژول‌ها

<div dir="rtl">

| فایل | نقش |
|---|---|
| `src/main.py` | نقطه‌ی ورود: خواندن آرگومان‌ها، تشخیص نسخه‌ی در حال اجرا، انتخاب پورت آزاد، باز کردن رابط، `--setup` و `--diagnose` |
| `cyrusasha_odl/app.py` | ترکیب و اتصال همه‌ی مدیرها، شمارنده‌ی تغییر وضعیت، گزارش برنامه |
| `cyrusasha_odl/server.py` | سرور HTTP، مسیرهای API، فایل‌های رابط، بررسی Host و سرآیند CSRF |
| `cyrusasha_odl/config.py` | مسیرها، تنظیمات (`settings.json`)، اعتبارسنجی، پیش‌تنظیم‌ها |
| `cyrusasha_odl/env.py` | تشخیص Java، پایتون، بسته‌های محیط مجازی و آخرین نسخه‌ی PyPI (با cache) |
| `cyrusasha_odl/setup_tasks.py` | نصب‌کننده: مراحل، دانلود با checksum، Java قابل‌حمل، winget، آزمون خودکار، بررسی اولیه‌ی هیبرید |
| `cyrusasha_odl/jobs.py` | صف کارها، ساخت آرگومان‌های CLI، تقسیم به دسته، تحلیل گزارش، خروجی‌ها، بارگذاری‌ها |
| `cyrusasha_odl/hybrid.py` | اجرا/توقف سرور هیبرید، بررسی `/health`، اجرای خودکار برای کارها |
| `cyrusasha_odl/rules.py` | ذخیره و اعتبارسنجی قوانین پاک‌سازی |
| `cyrusasha_odl/rtl.py` | اصلاح خروجی فارسی/عربی/عبری: الگوریتم دوجهتی یونیکد، حروف و نیم‌فاصله، لیگاتورها، جدول‌ها؛ فهرست واژه در `data/rtl-words.txt.gz` |
| `cyrusasha_odl/storage.py` | پوشه‌ی نصب و پوشه‌ی مدل‌ها: حجم، فضای آزاد، بررسی پوشه‌ی جدید، انتقال با پیشرفت و متغیرهای محیطی دانلود مدل‌ها |
| `cyrusasha_odl/procs.py` | اجرای پردازه‌ها با خواندن زنده‌ی خروجی و توقف کل درخت پردازه |
| `cyrusasha_odl/platform_utils.py` | کارهای وابسته به سیستم‌عامل: رجیستری ویندوز، پنجره‌ی انتخاب فایل، Explorer، میان‌بر، Edge |
| `src/gui/public/js/*.js` | رابط: `app.js` (مسیریابی، وضعیت، هدر/فوتر)، `page-*.js` (صفحه‌ها)، `i18n.js` (فارسی/انگلیسی) |

</div>

| File | Role |
|---|---|
| `src/main.py` | Entry point: arguments, running-instance detection, free-port selection, UI launch, `--setup` and `--diagnose` |
| `cyrusasha_odl/app.py` | Composition root wiring all managers, state revision counter, application log |
| `cyrusasha_odl/server.py` | HTTP server, API routes, static UI files, Host check and CSRF header |
| `cyrusasha_odl/config.py` | Paths, settings (`settings.json`), validation, presets |
| `cyrusasha_odl/env.py` | Detection of Java, Python, venv packages and the latest PyPI version (cached) |
| `cyrusasha_odl/setup_tasks.py` | Installer: steps, checksum-verified downloads, portable Java, winget, self-test, hybrid pre-check |
| `cyrusasha_odl/jobs.py` | Job queue, CLI argument building, batching, log parsing, outputs, uploads |
| `cyrusasha_odl/hybrid.py` | Start/stop of the hybrid server, `/health` polling, auto-start for jobs |
| `cyrusasha_odl/rules.py` | Storage and validation of sanitize rules |
| `cyrusasha_odl/rtl.py` | Repair of Persian/Arabic/Hebrew output: Unicode bidi algorithm, letters and ZWNJ, ligatures, tables; word list in `data/rtl-words.txt.gz` |
| `cyrusasha_odl/storage.py` | Install and models folders: sizes, free space, checks of a new folder, moving with progress and the environment variables for model downloads |
| `cyrusasha_odl/procs.py` | Child processes with live output capture and process-tree termination |
| `cyrusasha_odl/platform_utils.py` | OS specifics: Windows registry, file dialogs, Explorer, shortcuts, Edge |
| `src/gui/public/js/*.js` | UI: `app.js` (routing, state, header/footer), `page-*.js` (pages), `i18n.js` (Persian/English) |

---

## 🔄 State and polling / وضعیت و به‌روزرسانی

<div dir="rtl">

رابط هر حدود ۰٫۸ ثانیه (هنگام کار) یا ۲٫۵ ثانیه (بیکار) نشانی `/api/state` را می‌خواند؛ این پاسخ ارزان است چون هیچ پردازه‌ی خارجی اجرا نمی‌کند و فقط داده‌های cache‌شده را برمی‌گرداند. گزارش‌ها به‌صورت افزایشی با `?since=N` خوانده می‌شوند تا حتی گزارش‌های طولانی هم سبک بمانند.

</div>

The UI polls `/api/state` about every 0.8 s while work is running (2.5 s when idle); the response is cheap because it starts no external processes and only returns cached data. Logs are read incrementally with `?since=N`, so even very long logs stay lightweight.

---

## 📋 Job lifecycle / چرخه‌ی عمر کار

<div dir="rtl">

۱. **ایجاد:** ورودی‌ها بررسی می‌شوند (وجود، PDF بودن، تعداد PDFهای پوشه) و گزینه‌ها با `OPTION_SPEC` و فهرست گزینه‌های نسخه‌ی نصب‌شده اعتبارسنجی می‌شوند؛ سپس کار با وضعیت `queued` ذخیره می‌شود.

۲. **زمان‌بندی:** زمان‌بند تا سقف «کارهای هم‌زمان» کار شروع می‌کند و تا وقتی نصب‌کننده محیط را تغییر می‌دهد صبر می‌کند.

۳. **هیبرید:** اگر کار هیبرید باشد و نشانی خارجی نداشته باشد، سرور مدیریت‌شده در صورت نیاز اجرا می‌شود و کار با وضعیت `waiting` تا آماده شدن آن صبر می‌کند.

۴. **اجرا:** همه‌ی ورودی‌ها در یک فراخوانی `python -m opendataloader_pdf` اجرا می‌شوند؛ فقط فهرست‌های خیلی طولانی برای ماندن زیر محدودیت ۳۲٬۷۶۷ نویسه‌ای خط فرمان ویندوز به چند دسته تقسیم می‌شوند.

۵. **تحلیل گزارش:** متغیر `JAVA_TOOL_OPTIONS` گزارش Java را انگلیسی، UTF-8 و تک‌خطی می‌کند؛ از خطوط `File name`، `Number of pages` و `Created` پیشرفت و نتیجه‌ی هر فایل استخراج می‌شود و خطوط تکراری فشرده می‌شوند.

۶. **اصلاح راست‌به‌چپ:** اگر خروجی یک PDF حروف راست‌به‌چپ داشته باشد (و گزینه‌ی `rtl-fix` خاموش نباشد)، کار در وضعیت `running` و مرحله‌ی `phase: "rtl"` می‌ماند تا خروجی‌ها اصلاح شوند (بخش بعد).

۷. **پایان:** وضعیت نهایی `success`، `partial` (برخی فایل‌ها ناموفق)، `failed` یا `canceled` است و فهرست خروجی‌ها در `job.json` ذخیره می‌شود.

</div>

1. **Create:** inputs are inspected (existence, PDF type, number of PDFs in folders) and options are validated against `OPTION_SPEC` and the option list of the installed version; the job is stored as `queued`.
2. **Schedule:** the scheduler starts jobs up to the “parallel jobs” limit and waits while the installer changes the environment.
3. **Hybrid:** for hybrid jobs without an external URL, the managed server is started when needed and the job waits as `waiting` until it is ready.
4. **Run:** all inputs go into one `python -m opendataloader_pdf` call; only very long lists are split into batches to stay below the 32,767-character Windows command-line limit.
5. **Parse:** `JAVA_TOOL_OPTIONS` makes the Java log English, UTF-8 and one line per record; `File name`, `Number of pages` and `Created` lines provide progress and per-file results, and repeated lines are collapsed.
6. **RTL repair:** if the outputs of a PDF contain right-to-left letters (and the `rtl-fix` option is not off), the job stays `running` with `phase: "rtl"` until the outputs are repaired (next section).
7. **Finish:** the final state is `success`, `partial` (some files failed), `failed` or `canceled`, and the output list is saved to `job.json`.

---

## 🔤 RTL output repair / اصلاح خروجی راست‌به‌چپ

<div dir="rtl">

opendataloader-pdf تکه‌های متن هر سطر را از چپ به راست مرتب می‌کند و برای فارسی، عربی و عبری «ترتیب دیداری» برمی‌گرداند (حروف وارونه، شکل‌های نمایشی، بدون نیم‌فاصله، ستون‌های جدول از چپ). `JobManager._repair_rtl` پس از اجرای CLI این خروجی‌ها را با `rtl.OutputRepair` اصلاح می‌کند:

۱. **انتخاب:** از خطوط `File name` و `Created` گزارش، فهرست خروجی‌های هر PDF ساخته می‌شود (`file_results[i].source/files`). PDFهایی که خروجی متنی‌شان حداقل ۱۰ حرف راست‌به‌چپ دارد انتخاب می‌شوند؛ اگر دو PDF هم‌نام خروجی یکسانی نوشته باشند، فقط آخرین نویسنده اصلاح می‌شود.

۲. **داده‌ی سطرها:** چون پس از چسباندن سطرها مرز سطر از دست می‌رود، `_line_pass` برای همین PDFها CLI را با همان گزینه‌ها و `--format json --keep-line-breaks --image-output off` در یک پوشه‌ی موقت اجرا می‌کند (اگر کاربر خودش `--keep-line-breaks` و JSON خواسته باشد، همان JSON استفاده می‌شود). آزمون‌ها نشان داده‌اند که هر دو اجرا عناصر یکسان و به ترتیب یکسان تولید می‌کنند و `join_lines` دقیقاً همان چسباندن opendataloader-pdf است (`TextChunkUtils.formatLineEnd`).

۳. **اصلاح هر عنصر:** جهت پاراگراف و دیداری/منطقی بودن متن تشخیص داده می‌شود (`paragraph_is_rtl`، `order_evidence`)، هر سطر با UAX #9 برگردانده می‌شود، شکل‌های نمایشی به حروف و نیم‌فاصله‌های اثبات‌شده بازمی‌گردند (`normalize_text`) و لیگاتورهای جابه‌جاشده با فهرست واژه درست می‌شوند (`fix_ligatures`). قرارداد پرانتزها (آینه‌شده در Chrome، اصلی در InDesign) برای هر سند از تعداد پرانتزهای جفت‌نشده انتخاب می‌شود.

۴. **بازنویسی:** JSON با پیمایش هم‌زمان دو درخت و با همان قالب Jackson نوشته می‌شود؛ در Markdown، HTML و متن، متن اصلی هر عنصر (با escape همان قالب) به ترتیب سند جست‌وجو و جایگزین می‌شود و بخش‌های پیدا‌نشده سطربه‌سطر اصلاح می‌شوند. جدول‌های راست‌به‌چپ وارونه می‌شوند و HTML ویژگی `lang`/`dir` می‌گیرد. خلاصه در `job.rtl` و `file_results[i].rtl` ذخیره می‌شود.

فهرست واژه با `scripts/build-rtl-lexicon.py` از متن ویکی‌پدیا و فهرست‌های بسامد ساخته می‌شود (۳۰٬۰۰۰ واژه‌ی فارسی، ۲۰٬۰۰۰ واژه‌ی عربی، به ترتیب بسامد). قاعده‌ی اصلی: واژه‌ای که در فهرست هست هرگز تغییر نمی‌کند.

</div>

opendataloader-pdf sorts the text pieces of every line from left to right and returns Persian, Arabic and Hebrew in *visual* order (reversed letters, presentation forms, no ZWNJ, table columns from the left). After the CLI run, `JobManager._repair_rtl` repairs these outputs with `rtl.OutputRepair`:

1. **Selection:** the `File name` and `Created` log lines give the outputs of every PDF (`file_results[i].source/files`). PDFs whose text outputs contain at least 10 right-to-left letters are selected; if two PDFs with the same name wrote the same outputs, only the last writer is repaired.
2. **Line data:** line boundaries are lost once lines are joined, so `_line_pass` runs the CLI again for these PDFs with the same options plus `--format json --keep-line-breaks --image-output off` into a temporary folder (if the user asked for `--keep-line-breaks` and JSON, that JSON is used directly). Tests confirmed that both runs produce the same elements in the same order and that `join_lines` reproduces the joining of opendataloader-pdf exactly (`TextChunkUtils.formatLineEnd`).
3. **Element repair:** the paragraph direction and whether the text is visual or logical are detected (`paragraph_is_rtl`, `order_evidence`); every line is reordered with UAX #9, presentation forms become letters and proven ZWNJs are restored (`normalize_text`), and swapped ligature letters are fixed with the word list (`fix_ligatures`). The bracket convention (mirrored in Chrome, original in InDesign) is chosen per document from the number of unmatched brackets.
4. **Rewrite:** JSON is rewritten by walking both trees in parallel, in the exact Jackson layout; in Markdown, HTML and text the original text of every element (escaped like the writer does) is searched in document order and replaced, and parts that are not found are repaired line by line. RTL tables are reversed and HTML gets `lang`/`dir`. A summary is stored in `job.rtl` and `file_results[i].rtl`.

The word list is built by `scripts/build-rtl-lexicon.py` from Wikipedia text and frequency lists (30,000 Persian and 20,000 Arabic words, most frequent first). The main rule: a word that is in the list is never changed.

---

## 📦 Installer / نصب‌کننده

<div dir="rtl">

هر عملیات نصب مجموعه‌ای از مراحل است (مثلاً `java → venv → pip → package → hybrid → verify`) که به‌ترتیب و در یک رشته‌ی پس‌زمینه اجرا می‌شوند. دانلود Java از API رسمی Adoptium انجام و با SHA-256 بررسی می‌شود و استخراج آرشیو در برابر «zip slip» محافظت شده است. پیش از نصب هیبرید، `pip install --dry-run --report` بسته‌هایی را که نسخه‌ی آماده (wheel) ندارند پیدا می‌کند.

</div>

Every installer action is a sequence of steps (e.g. `java → venv → pip → package → hybrid → verify`) executed in order on a background thread. Java is downloaded from the official Adoptium API and verified with SHA-256, and archive extraction is protected against “zip slip”. Before the hybrid install, `pip install --dry-run --report` finds packages that have no prebuilt wheel.

---

## 🔒 Security model / مدل امنیتی

<div dir="rtl">

- سرور به‌طور پیش‌فرض فقط روی `127.0.0.1` گوش می‌دهد و تا وقتی روی loopback است، فقط سرآیند `Host` برابر `localhost` یا `127.0.0.1` پذیرفته می‌شود.
- هر درخواست POST/PUT/DELETE باید سرآیند `X-CyrusAsha-ODL: 1` داشته باشد؛ مرورگرها این سرآیند را بدون CORS pre-flight به درخواست‌های بین‌سایتی اضافه نمی‌کنند و سرور هم CORS را نمی‌پذیرد.
- فایل‌های خروجی فقط از پوشه‌ی خروجی همان کار سرو می‌شوند (بررسی مسیر واقعی) و HTML/SVG تولیدشده با `Content-Security-Policy: sandbox` نمایش داده می‌شود.
- گزینه‌ها به شکل `--name=value` و بدون shell منتقل می‌شوند و رمز PDF فقط در حافظه نگه داشته می‌شود.

</div>

- By default the server listens on `127.0.0.1` only, and while bound to loopback it accepts only `Host` headers equal to `localhost` or `127.0.0.1`.
- Every POST/PUT/DELETE request must carry `X-CyrusAsha-ODL: 1`; browsers never add this header to cross-site requests without a CORS pre-flight, and the server does not accept CORS.
- Output files are served only from the respective job’s output folder (real-path check), and generated HTML/SVG is served with `Content-Security-Policy: sandbox`.
- Options are passed as `--name=value` without a shell, and PDF passwords are kept in memory only.

---

## 💾 Data folder / پوشه‌ی داده

<div dir="rtl">

همه‌ی داده‌ها زیر یک پوشه (پیش‌فرض `data/` در ریشه‌ی پروژه، قابل تغییر با `--data` یا `CYRUSASHA_ODL_DATA`) نگه داشته می‌شوند. فایل‌های JSON به‌صورت اتمی (فایل موقت + جایگزینی) نوشته می‌شوند تا قطع برق هیچ فایل نیمه‌کاره‌ای باقی نگذارد.

</div>

All data lives below one folder (default `data/` in the project root, configurable with `--data` or `CYRUSASHA_ODL_DATA`). JSON files are written atomically (temporary file + rename), so a power loss can never leave a truncated file behind.

```text
data/
├─ settings.json · presets.json
├─ venv/              Python environment with opendataloader-pdf   ┐
├─ java/              portable Eclipse Temurin runtime             │ install folder (setting install_dir)
├─ cache/             pip download cache (pip/) and pip temp (tmp/) ┘
├─ models/            models downloaded by the hybrid server         - models folder (setting models_dir)
├─ jobs/<id>/         job.json · log.txt · output/
├─ uploads/<batch>/   files dragged into the browser
├─ rules/             sanitize rule files
├─ logs/              app.log · setup-*.log · hybrid-server.log
└─ tmp/               downloads and self-test files
```

<div dir="rtl">

**پوشه‌ی نصب و پوشه‌ی مدل‌ها.** `venv`، `java` و `cache` با هم «پوشه‌ی نصب» را می‌سازند و `models` «پوشه‌ی مدل‌ها» است؛ هر دو با تنظیم‌های `install_dir` و `models_dir` قابل جابه‌جایی‌اند (مقدار خالی = پیش‌فرض: `data` و `<پوشه‌ی نصب>/models`). `Paths.apply_locations()` مسیرها را پس از خواندن تنظیمات محاسبه می‌کند و همه‌ی مدیرها مسیرها را هنگام استفاده از `paths` می‌خوانند، پس تغییر بی‌درنگ اعمال می‌شود. `EnvInspector.child_env()` برای هر پردازه‌ی فرزند `HF_HOME`، `EASYOCR_MODULE_PATH`، `DOCLING_CACHE_DIR`، `MODELSCOPE_CACHE`، `TORCH_HOME` و `XDG_CACHE_HOME` را روی پوشه‌ی مدل‌ها و برای pip (`install=True`) `PIP_CACHE_DIR` و `TMP`/`TEMP` را روی پوشه‌ی نصب تنظیم می‌کند.

**تغییر محل** از مسیر `/api/storage/change` و به‌صورت عملیات نصب `relocate` اجرا می‌شود (پس هم‌زمان با کارها اجرا نمی‌شود و سرور هیبرید پیش از آن متوقف می‌شود). `storage.check_target()` پوشه‌ی جدید را بررسی می‌کند (مسیر کامل، داخل برنامه نبودن، تودرتو نبودن با پوشه‌هایی که منتقل می‌شوند، قابل نوشتن بودن، فضای آزاد) و هشدار `long_path` (ویندوز بدون LongPathsEnabled و مسیر بلند)، `non_ascii`، `network` و `drive_missing` می‌دهد. `move_folder()` روی همان درایو تغییر نام و در غیر این صورت کپی تکه‌تکه با پیشرفت و لغو انجام می‌دهد (پیوندهای نمادین حفظ می‌شوند)؛ `_move_items()` پیش از شروع، تداخل و فضای آزاد را بررسی و در صورت خطا پوشه‌های منتقل‌شده را برمی‌گرداند. تنظیم فقط پس از انتقال موفق تغییر می‌کند. نصب تازه می‌تواند `install_dir`/`models_dir` را همراه عملیات `full` یا `install` بفرستد (پنجره‌ی نصب، `install.bat`)؛ گام اول `location` آن‌ها را بدون انتقال اعمال می‌کند. بررسی پوشه چیزی نمی‌سازد: فایل آزمایشی فقط در نزدیک‌ترین پوشه‌ی موجود نوشته و پاک می‌شود. محیط مجازی پس از انتقال کار می‌کند، چون برنامه همیشه `venv/python -m …` را اجرا می‌کند و هیچ‌گاه launcherهای `Scripts\*.exe` را که مسیر مطلق دارند به کار نمی‌برد.

</div>

**Install and models folders.** `venv`, `java` and `cache` form the *install folder*, `models` the *models folder*; both can be moved with the settings `install_dir` and `models_dir` (empty = default: `data` and `<install folder>/models`). `Paths.apply_locations()` computes the paths after the settings are loaded, and every manager reads them from `paths` when it needs them, so a change takes effect at once. `EnvInspector.child_env()` points `HF_HOME`, `EASYOCR_MODULE_PATH`, `DOCLING_CACHE_DIR`, `MODELSCOPE_CACHE`, `TORCH_HOME` and `XDG_CACHE_HOME` of every child process to the models folder, and for pip (`install=True`) `PIP_CACHE_DIR` and `TMP`/`TEMP` to the install folder.

**Changing a folder** goes through `/api/storage/change` and runs as the installer action `relocate` (so it never runs alongside jobs, and the hybrid server is stopped first). `storage.check_target()` validates the new folder (absolute path, outside the program, not nested with a folder that moves, writable, free space) and warns with `long_path` (Windows without LongPathsEnabled and a long path), `non_ascii`, `network` and `drive_missing`. `move_folder()` renames on the same drive and otherwise copies in chunks with progress and cancellation (symbolic links are kept); `_move_items()` checks conflicts and free space before anything moves and moves already moved folders back on failure. The setting only changes after a successful move. A fresh installation can pass `install_dir`/`models_dir` with the `full` or `install` action (install dialog, `install.bat`); the first step `location` applies them without moving anything. Checking a folder creates nothing: a probe file is written to (and removed from) the nearest existing folder only. A moved virtual environment keeps working because the program always runs `venv/python -m …` and never the `Scripts\*.exe` launchers, which contain absolute paths.

---

## 🔌 HTTP API / رابط HTTP

<div dir="rtl">

همه‌ی پاسخ‌ها JSON هستند؛ خطاها به شکل `{"error": "<code>", "detail": "…"}` برمی‌گردند و رابط، کد خطا را با کلید `err.<code>` به فارسی یا انگلیسی ترجمه می‌کند.

</div>

All responses are JSON; errors are returned as `{"error": "<code>", "detail": "…"}` and the UI translates the code into Persian or English with the key `err.<code>`.

| Method | Path | Purpose / کاربرد |
|---|---|---|
| GET | `/api/ping`, `/api/info`, `/api/state` | identity, static info, overall state / شناسه، اطلاعات ثابت، وضعیت کلی |
| GET | `/api/env?refresh=1`, `/api/pythons`, `/api/versions` | environment report, interpreters, PyPI versions / گزارش محیط، مفسرها، نسخه‌ها |
| GET · POST | `/api/settings` | read / save settings (`install_dir`/`models_dir` only through `/api/storage/change`) / خواندن و ذخیره‌ی تنظیمات |
| POST | `/api/setup/start` `{action, params}` · `/api/setup/cancel` | start / cancel installer actions (incl. `relocate`, `clear-cache`, `clear-models`) / شروع و لغو نصب |
| GET | `/api/storage?refresh=1` | install/models folders: paths, sizes (`pending` while measuring), free space, warnings / پوشه‌ی نصب و مدل‌ها |
| POST | `/api/storage/check` `{kind, path}` · `/api/storage/change` `{kind, path, mode, delete_old}` | check a new folder · move (`move`) or switch (`fresh`) / بررسی و تغییر پوشه |
| GET | `/api/setup/status` · `/api/setup/log?since=` | installer state and log / وضعیت و گزارش نصب |
| GET | `/api/options` | CLI option specification / مشخصات گزینه‌ها |
| GET · POST · DELETE | `/api/presets[/id]` | presets / پیش‌تنظیم‌ها |
| PUT | `/api/upload?batch=&path=` | raw-body upload / بارگذاری فایل |
| POST | `/api/uploads/clear` · `/api/pick` · `/api/inspect` | cleanup, native dialog, path check / پاک‌سازی، پنجره‌ی انتخاب، بررسی مسیر |
| GET · POST | `/api/jobs` · `/api/jobs/preview` · `/api/jobs/clear` | list / create, command preview, delete finished / فهرست و ساخت، پیش‌نمایش فرمان، حذف |
| GET · DELETE | `/api/jobs/<id>` | job details / delete / جزئیات و حذف کار |
| GET | `/api/jobs/<id>/log` · `/files` · `/zip` | log, outputs, ZIP / گزارش، خروجی‌ها، ZIP |
| POST | `/api/jobs/<id>/cancel` · `/rerun` · `/open` | cancel, re-run, open / لغو، اجرای دوباره، باز کردن |
| GET | `/files/<id>/<path>` | output file (`?download=1`) / دریافت فایل خروجی |
| GET · POST | `/api/hybrid` · `/start` · `/stop` · `/restart` · `/preview` · `/check` · `/log` | hybrid server / سرور هیبرید |
| GET · PUT · DELETE | `/api/rules[/name]` · `/api/rules/test` · `/download` | sanitize rules / قوانین پاک‌سازی |
| GET · POST | `/api/logs/app` · `/api/open` · `/api/shortcut` · `/api/shutdown` | app log, open folder, shortcut, exit / گزارش، پوشه، میان‌بر، خروج |

---

## 🧱 Extending / توسعه و گسترش

<div dir="rtl">

- **گزینه‌ی تازه‌ی CLI:** به `OPTION_SPEC` در `jobs.py` اضافه کنید و برچسب `opt.<name>` را در هر دو زبان `i18n.js` بنویسید؛ گزینه‌های ناشناخته‌ی نسخه‌ی نصب‌شده خودکار در گروه «سایر» ظاهر می‌شوند.
- **گزینه‌ی مخصوص رابط:** با `"gui": True` در `OPTION_SPEC` (مثل `rtl-fix`) ذخیره و اعتبارسنجی می‌شود ولی به CLI نمی‌رسد.
- **پیش‌تنظیم داخلی:** به `BUILTIN_PRESETS` در `config.py` اضافه کنید.
- **متن تازه‌ی رابط:** کلید را در هر دو بخش `en` و `fa` فایل `i18n.js` اضافه کنید؛ آزمون `test/test_i18n.py` کامل بودن هر دو زبان را بررسی می‌کند.
- **مستندسازی:** هر تابع پایتون docstring و هر تابع جاوااسکریپت بلوک JSDoc دارد؛ کد تازه هم باید همین قاعده را رعایت کند.

</div>

- **New CLI option:** add it to `OPTION_SPEC` in `jobs.py` and write the `opt.<name>` label in both languages of `i18n.js`; unknown options of the installed version appear automatically in the “Other” group.
- **GUI-only option:** `"gui": True` in `OPTION_SPEC` (like `rtl-fix`) stores and validates the option but never passes it to the CLI.
- **New built-in preset:** add it to `BUILTIN_PRESETS` in `config.py`.
- **New UI text:** add the key to both the `en` and the `fa` section of `i18n.js`; `test/test_i18n.py` checks that both languages are complete.
- **Documentation:** every Python function has a docstring and every JavaScript function a JSDoc block; new code must follow the same rule.
