# sushijobs.cc: website tasks

**Goal:** turn website visitors into downloads and GitHub stars, and make Sushi findable on Google and Bing.

**Repo:** https://github.com/getsushi/sushi
**Latest release:** https://github.com/getsushi/sushi/releases/latest

Checked 2026-11-20. The site has a `<title>`, a meta description and one `<h1>`. Everything below is missing.

---

## P0: do first (this week)

### 1. Remove "coming soon"
- [ ] The meta description ends with "A local-first desktop app, coming soon." v0.1.0 is released, so remove "coming soon" from the meta description and any page copy.

### 2. Download button
- [ ] Add a primary **Download for Windows** button in the hero, linking to `https://github.com/getsushi/sushi/releases/latest`.
- [ ] Add a secondary link, "macOS / Linux: run from source", to `https://github.com/getsushi/sushi#installation`.
- [ ] Below the button, a small note: "Windows 10/11, 64-bit · Free and open source · MIT".

### 3. GitHub links
There is currently no link to the repo anywhere on the site.
- [ ] Add a **⭐ Star on GitHub** button in the header (top right) and in the hero.
- [ ] Optional: show the live star count with a badge
  (`https://img.shields.io/github/stars/getsushi/sushi?style=social`) or the GitHub API, cached.
- [ ] Footer links: GitHub, Releases, Issues, Contributing, Security, Licence (MIT).

### 4. robots.txt
`/robots.txt` currently returns 404.
- [ ] Serve this at `/robots.txt`:
  ```
  User-agent: *
  Allow: /
  Sitemap: https://sushijobs.cc/sitemap.xml
  ```

### 5. sitemap.xml
`/sitemap.xml` currently returns 404.
- [ ] Generate a sitemap listing every public page, with `lastmod` dates. If the site is Next.js, use `app/sitemap.ts`.

### 6. Social sharing tags
There are no Open Graph or Twitter tags, so shared links show no preview card.
- [ ] Add to `<head>`:
  ```html
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://sushijobs.cc/">
  <meta property="og:title" content="Sushi: open-source AI job application autofill">
  <meta property="og:description" content="Upload your CV once. Sushi finds jobs that fit and fills in the applications for you. Free, open source and local-first.">
  <meta property="og:image" content="https://sushijobs.cc/og.png">
  <meta name="twitter:card" content="summary_large_image">
  ```
- [ ] Design `og.png` at **1280×640 or 1200×630**: logo, tagline and one app screenshot. Reuse the same image as the GitHub social preview.

### 7. Canonical URL and favicon
- [ ] Add `<link rel="canonical" href="https://sushijobs.cc/">` to every page, pointing to that page's own URL.
- [ ] `/favicon.ico` returns 404. Serve one, as some browsers and crawlers still request it.
- [ ] Add an `apple-touch-icon` at 180×180.
- [ ] Make sure `www.sushijobs.cc` redirects to `sushijobs.cc` (or the other way round) with a 301.

---

## P1: search ranking (next 2 weeks)

### 8. Title and description wording
Use the words people search for.
- [ ] Title (under 60 characters):
  `Sushi: Free AI Job Application Autofill (Open Source)`
- [ ] Meta description (under 155 characters):
  `Upload your CV once. Sushi finds matching jobs and auto-fills applications on Greenhouse, Lever, Workday and more. Free, open source, private.`

### 9. Page content
The `<h1>` loads with `opacity:0` and fades in. Confirm it is in the server-rendered HTML, not added by JavaScript.
- [ ] Below the hero, add short text sections with `<h2>` headings that use search phrases:
  - "Auto-apply to jobs from your CV"
  - "Works with Greenhouse, Lever, Ashby, Workday and 9 more ATS"
  - "Your data stays on your computer"
  - "Use free models: Groq, Gemini or a local Ollama model"
  - "How it works": 3 steps with screenshots
- [ ] Add an **FAQ** section. Each question is a search query:
  - Is Sushi free?
  - Does Sushi submit applications without me?
  - Which job sites does it support?
  - Is my CV sent anywhere?
  - Does it work on Mac?
- [ ] Give every image descriptive `alt` text.

### 10. Structured data (JSON-LD)
- [ ] Add a `SoftwareApplication` schema to the home page:
  ```html
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    "name": "Sushi",
    "applicationCategory": "BusinessApplication",
    "operatingSystem": "Windows, macOS, Linux",
    "offers": { "@type": "Offer", "price": "0", "priceCurrency": "USD" },
    "downloadUrl": "https://github.com/getsushi/sushi/releases/latest",
    "softwareVersion": "0.1.0",
    "license": "https://opensource.org/licenses/MIT",
    "url": "https://sushijobs.cc/",
    "sameAs": ["https://github.com/getsushi/sushi"]
  }
  </script>
  ```
- [ ] Add an `FAQPage` schema for the FAQ section.
- [ ] Check both with Google's Rich Results Test.

### 11. Search Console and Bing
- [ ] Verify the domain in **Google Search Console** (DNS TXT record), submit `sitemap.xml`, and request indexing of the home page.
- [ ] Do the same in **Bing Webmaster Tools**. You can import the site from Search Console.
- [ ] Check the Coverage report after a week and fix anything excluded.

### 12. Performance
- [ ] Lighthouse score of **90 or more** on mobile for Performance, SEO and Accessibility. Attach screenshots to the pull request.
- [ ] Serve images as WebP or AVIF, with width and height set.
- [ ] Lazy-load any demo GIF or video below the fold. Prefer an MP4 or WebM loop over a GIF.

---

## P2: growth (later)

### 13. Pages that rank on their own
- [ ] `/docs` or `/guide`: installation, choosing a model, first application.
- [ ] `/changelog`, generated from GitHub releases.
- [ ] Blog: posts such as "How to auto-apply to Workday jobs" or "Free AI job application tools compared".
- [ ] One page per ATS, such as `/ats/workday` and `/ats/greenhouse`: "Auto-fill Workday applications with Sushi".
- [ ] Comparison pages: "Sushi vs LazyApply", "Sushi vs Simplify". Keep them factual.

### 14. Trust
- [ ] A privacy page explaining what stays local and what goes to the model provider.
- [ ] A "Responsible use" note: review before submit, a daily cap, and respecting job sites' terms.
- [ ] The SHA-256 checksum next to the download button.

### 15. Analytics
- [ ] A privacy-friendly, cookie-free analytics tool (for example Plausible, Umami or GoatCounter), so no cookie banner is needed.
- [ ] Track clicks on: Download, Star on GitHub, and View docs.

---

## Definition of done
- [ ] `/robots.txt` and `/sitemap.xml` return 200.
- [ ] A shared link shows the preview card. Test with opengraph.xyz, the LinkedIn Post Inspector and the X Card Validator.
- [ ] The Download and Star buttons work on desktop and mobile.
- [ ] Rich Results Test passes for the `SoftwareApplication` schema.
- [ ] Lighthouse mobile SEO score is 95 or more.
- [ ] Sitemap submitted in Google Search Console and Bing Webmaster Tools.
