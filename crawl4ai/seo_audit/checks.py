"""
Per-page SEO checks.

Ported from seo-audit-mcp's crawl-page.ts, browser.ts, and page-capture.ts.
Operates on raw HTML + CrawlResult metadata to produce PageAuditResult.
"""

import re
import json
import hashlib
import posixpath
from collections import Counter
from copy import deepcopy
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse, urljoin

from lxml import html as lxml_html
from lxml.html import HtmlElement

from .models import (
    CheckStatus,
    PageAuditResult,
    TitleCheck,
    MetaDescriptionCheck,
    CanonicalCheck,
    RobotsCheck,
    HeadingCheck,
    HeadingInfo,
    ImageCheck,
    ImageInfo,
    LinkStats,
    LinkDetail,
    OpenGraphCheck,
    TwitterCardCheck,
    StructuredDataCheck,
    StructuredDataItem,
    HreflangCheck,
    HreflangEntry,
    ContentCheck,
    URLCheck,
    MixedContentCheck,
    ViewportCheck,
    LangCheck,
    CharsetCheck,
    PerformanceCheck,
)


def _parse_html(raw_html: str) -> Optional[HtmlElement]:
    """Parse HTML string into an lxml tree, returning None on failure."""
    try:
        return lxml_html.fromstring(raw_html)
    except Exception:
        return None


# ─── Individual Checks ────────────────────────────────────────────────


def check_title(tree: HtmlElement) -> TitleCheck:
    """Check title tag presence and length (target: 30-65 chars)."""
    titles = tree.xpath("//title/text()")
    value = titles[0].strip() if titles else None
    if not value:
        return TitleCheck(status=CheckStatus.FAIL, note="Missing title tag")

    length = len(value)
    if 30 <= length <= 65:
        status, note = CheckStatus.PASS, "Good length"
    elif 20 <= length < 30:
        status, note = CheckStatus.WARNING, f"Slightly short ({length} chars)"
    elif 65 < length <= 80:
        status, note = CheckStatus.WARNING, f"Slightly long ({length} chars)"
    elif length < 20:
        status, note = CheckStatus.WARNING, f"Too short ({length} chars)"
    else:
        status, note = CheckStatus.WARNING, f"Too long ({length} chars, may be truncated)"

    return TitleCheck(value=value, length=length, status=status, note=note)


def check_meta_description(tree: HtmlElement) -> MetaDescriptionCheck:
    """Check meta description presence and length (target: 50-160 chars)."""
    descs = tree.xpath('//meta[@name="description"]/@content')
    value = descs[0].strip() if descs else None
    if not value:
        return MetaDescriptionCheck(
            status=CheckStatus.FAIL, note="Missing meta description"
        )

    length = len(value)
    if 50 <= length <= 160:
        status, note = CheckStatus.PASS, f"Good length ({length} chars)"
    elif length < 50:
        status, note = CheckStatus.WARNING, f"Too short ({length} chars, aim for 50-160)"
    elif 160 < length <= 200:
        status, note = CheckStatus.WARNING, f"Slightly long ({length} chars, may be truncated)"
    else:
        status, note = CheckStatus.WARNING, f"Too long ({length} chars, will be truncated by search engines)"

    return MetaDescriptionCheck(value=value, length=length, status=status, note=note)


def check_canonical(tree: HtmlElement, page_url: str) -> CanonicalCheck:
    """Check canonical tag presence, self-referencing, and multiple conflicting canonicals."""
    canonicals = tree.xpath('//link[@rel="canonical"]/@href')
    if not canonicals:
        return CanonicalCheck(
            status=CheckStatus.WARNING, note="Missing canonical tag"
        )

    # Bug 11: Detect multiple conflicting canonical tags
    unique_canonicals = list(dict.fromkeys(c.strip() for c in canonicals if c.strip()))
    if len(unique_canonicals) > 1:
        return CanonicalCheck(
            value=unique_canonicals[0],
            is_self_referencing=False,
            status=CheckStatus.WARNING,
            note=f"Multiple conflicting canonical tags ({len(unique_canonicals)} found): {', '.join(unique_canonicals[:3])}",
        )

    value = unique_canonicals[0] if unique_canonicals else None
    if not value:
        return CanonicalCheck(
            status=CheckStatus.WARNING, note="Missing canonical tag"
        )

    # Normalize for comparison
    parsed_page = urlparse(page_url)
    parsed_canon = urlparse(value)
    is_self = (
        parsed_page.netloc == parsed_canon.netloc
        and parsed_page.path.rstrip("/") == parsed_canon.path.rstrip("/")
    )

    if is_self:
        return CanonicalCheck(
            value=value,
            is_self_referencing=True,
            status=CheckStatus.PASS,
            note="Self-referencing canonical",
        )
    return CanonicalCheck(
        value=value,
        is_self_referencing=False,
        status=CheckStatus.INFO,
        note=f"Points to {value}",
    )


def check_robots_meta(tree: HtmlElement) -> RobotsCheck:
    """Check robots meta tag for noindex/nofollow directives."""
    robots = tree.xpath('//meta[@name="robots"]/@content')
    value = robots[0].strip().lower() if robots else None

    if not value:
        return RobotsCheck(status=CheckStatus.PASS, note="No robots meta (defaults to index,follow)")

    is_indexable = "noindex" not in value
    is_followable = "nofollow" not in value

    if is_indexable and is_followable:
        status, note = CheckStatus.PASS, "Indexable and followable"
    elif not is_indexable:
        status, note = CheckStatus.WARNING, "Page set to noindex"
    else:
        status, note = CheckStatus.INFO, "Links set to nofollow"

    return RobotsCheck(
        value=value,
        is_indexable=is_indexable,
        is_followable=is_followable,
        status=status,
        note=note,
    )


def check_headings(tree: HtmlElement) -> HeadingCheck:
    """
    Check heading structure: H1 presence/count and hierarchy.
    Ported from seo-audit-mcp extractHeadings().
    """
    all_headings: List[HeadingInfo] = []
    h1_values: List[str] = []
    last_level = 0
    skipped: List[str] = []

    has_empty_h1 = False
    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for el in tree.xpath(f"//{tag}"):
            text = (el.text_content() or "").strip()
            level = int(tag[1])
            all_headings.append(HeadingInfo(tag=tag, text=text))
            if tag == "h1":
                if text:
                    h1_values.append(text)
                else:
                    has_empty_h1 = True
            if last_level and level > last_level + 1:
                skipped.append(f"h{last_level} -> h{level}")
            last_level = level

    # Re-sort by document order via xpath
    ordered_headings: List[HeadingInfo] = []
    for el in tree.xpath("//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]"):
        tag = el.tag.lower()
        text = (el.text_content() or "").strip()
        ordered_headings.append(HeadingInfo(tag=tag, text=text))

    # FN-8: Recompute skipped levels from ordered headings with improved detection
    skipped = []
    last_level = 0
    for h in ordered_headings:
        level = int(h.tag[1])
        if last_level == 0 and level > 1:
            # First heading should be H1; flag if it starts at a higher level
            skipped.append(f"h1 -> h{level} (first heading is not H1)")
        elif last_level and level > last_level + 1:
            skipped.append(f"h{last_level} -> h{level}")
        last_level = level

    h1_count = len(h1_values)
    hierarchy_valid = len(skipped) == 0

    if h1_count == 0 and has_empty_h1:
        status, note = CheckStatus.WARNING, "Empty H1 tag (element exists but contains no text)"
    elif h1_count == 0:
        status, note = CheckStatus.FAIL, "Missing H1 tag"
    elif h1_count > 1:
        status, note = CheckStatus.WARNING, f"Multiple H1 tags ({h1_count})"
    elif not hierarchy_valid:
        status, note = CheckStatus.WARNING, f"Skipped heading levels: {', '.join(skipped)}"
    elif h1_values and len(h1_values[0]) > 70:
        status, note = CheckStatus.WARNING, f"H1 too long ({len(h1_values[0])} chars)"
    else:
        status, note = CheckStatus.PASS, "Good heading structure"

    return HeadingCheck(
        h1_count=h1_count,
        h1_values=h1_values,
        has_empty_h1=has_empty_h1,
        hierarchy_valid=hierarchy_valid,
        skipped_levels=skipped,
        all_headings=ordered_headings,
        status=status,
        note=note,
    )


def check_images(tree: HtmlElement) -> ImageCheck:
    """
    Check images for alt text, dimensions, modern formats, size, and lazy loading.
    Ported from seo-audit-mcp extractImages(), extended with optimization checks.
    """
    MODERN_FORMATS = {".webp", ".avif", ".svg"}
    LEGACY_PHOTO_FORMATS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}
    OVERSIZED_THRESHOLD = 2000  # pixels — either dimension > 2000 is suspicious
    ALT_MAX_LENGTH = 125  # Google's practical display limit for alt text

    images: List[ImageInfo] = []
    missing_alt = 0
    empty_alt = 0
    long_alt = 0
    keyword_stuffed_alt = 0
    missing_dimensions = 0
    non_modern_format = 0
    potentially_oversized = 0
    missing_lazy_loading = 0
    broken_src = 0

    all_imgs = tree.xpath("//img")
    for idx, img in enumerate(all_imgs):
        src = img.get("src", "") or img.get("data-src", "")
        alt = img.get("alt")
        has_alt = alt is not None
        is_empty = has_alt and alt.strip() == ""
        is_lazy = bool(
            img.get("loading") == "lazy"
            or img.get("data-src")
            or img.get("data-lazy")
        )
        width = _int_or_none(img.get("width"))
        height = _int_or_none(img.get("height"))

        # Alt text checks
        if not has_alt:
            missing_alt += 1
        elif is_empty:
            empty_alt += 1
        elif alt and len(alt) > ALT_MAX_LENGTH:
            long_alt += 1

        # BUG-16: Keyword stuffing in alt text (same word repeated 3+ times)
        if alt and len(alt) > 20:
            alt_words = alt.lower().split()
            if len(alt_words) >= 5:
                from collections import Counter as _Counter
                word_freq = _Counter(w for w in alt_words if len(w) > 3)
                if word_freq and word_freq.most_common(1)[0][1] >= 3:
                    keyword_stuffed_alt += 1

        # BUG-17: Broken/empty src detection
        if not src or src.strip() in ("", "#"):
            broken_src += 1

        # Missing explicit width/height (causes CLS)
        has_dims = width is not None and height is not None
        if not has_dims:
            missing_dimensions += 1

        # Format detection
        ext = ""
        if src:
            path = urlparse(src).path
            ext = posixpath.splitext(path)[1].lower()
        is_modern = ext in MODERN_FORMATS or ext not in LEGACY_PHOTO_FORMATS
        if ext in LEGACY_PHOTO_FORMATS:
            non_modern_format += 1

        # Potentially oversized
        is_oversized = False
        if width and width > OVERSIZED_THRESHOLD or height and height > OVERSIZED_THRESHOLD:
            is_oversized = True
            potentially_oversized += 1

        # Missing lazy loading on below-the-fold images (heuristic: not first 2 imgs)
        needs_lazy = False
        if idx >= 2 and not is_lazy:
            needs_lazy = True
            missing_lazy_loading += 1

        images.append(
            ImageInfo(
                src=src,
                alt=alt,
                has_alt=has_alt,
                is_lazy_loaded=is_lazy,
                width=width,
                height=height,
                missing_dimensions=not has_dims,
                is_modern_format=is_modern,
                format_detected=ext.lstrip(".") if ext else "",
                is_potentially_oversized=is_oversized,
                needs_lazy_loading=needs_lazy,
            )
        )

    total = len(images)
    issues = []
    if missing_alt > 0:
        issues.append(f"{missing_alt} missing alt text")
    if long_alt > 0:
        issues.append(f"{long_alt} alt text too long (>{ALT_MAX_LENGTH} chars)")
    if keyword_stuffed_alt > 0:
        issues.append(f"{keyword_stuffed_alt} keyword-stuffed alt text")
    if missing_dimensions > 0:
        issues.append(f"{missing_dimensions} missing width/height")
    if non_modern_format > 0:
        issues.append(f"{non_modern_format} non-modern format")
    if potentially_oversized > 0:
        issues.append(f"{potentially_oversized} potentially oversized")
    if missing_lazy_loading > 0:
        issues.append(f"{missing_lazy_loading} missing lazy loading")
    if broken_src > 0:
        issues.append(f"{broken_src} broken/empty src")

    if missing_alt > 0 or missing_dimensions > 0 or long_alt > 0 or keyword_stuffed_alt > 0 or broken_src > 0:
        status = CheckStatus.WARNING
    elif issues:
        status = CheckStatus.INFO
    else:
        status = CheckStatus.PASS

    if issues:
        note = f"{total} images: {'; '.join(issues)}"
    else:
        note = f"All {total} images optimized" if total else "No images found"

    return ImageCheck(
        total=total,
        missing_alt=missing_alt,
        empty_alt=empty_alt,
        long_alt=long_alt,
        keyword_stuffed_alt=keyword_stuffed_alt,
        missing_dimensions=missing_dimensions,
        non_modern_format=non_modern_format,
        potentially_oversized=potentially_oversized,
        missing_lazy_loading=missing_lazy_loading,
        broken_src=broken_src,
        images=images,
        status=status,
        note=note,
    )


def _normalize_url(url: str) -> str:
    """Normalize a URL for consistent storage: lowercase domain, strip fragment, strip trailing slash."""
    parsed = urlparse(url)
    # Lowercase the domain, keep path as-is but strip trailing slash and fragment
    path = parsed.path.rstrip("/") or "/"
    normalized = parsed._replace(
        netloc=parsed.netloc.lower(),
        path=path,
        fragment="",
    )
    return normalized.geturl()


def check_links(
    tree: HtmlElement, page_url: str
) -> LinkStats:
    """
    Categorize internal/external/nofollow links and collect URLs.
    Now also captures per-link details (anchor_text, nofollow, link_type)
    for the link_graph feature. Tracks javascript: hrefs, empty hrefs,
    sponsored/ugc attributes, and internal nofollow links.
    """
    parsed_page = urlparse(page_url)
    page_domain = parsed_page.netloc.lower()

    internal = 0
    external = 0
    nofollow = 0
    javascript_href_count = 0
    empty_href_count = 0
    sponsored_count = 0
    ugc_count = 0
    internal_nofollow = 0
    internal_urls: List[str] = []
    external_urls: List[str] = []
    link_details: List[LinkDetail] = []
    seen_external_domains: Dict[str, str] = {}  # domain -> first URL (dedup externals)

    for a in tree.xpath("//a"):
        href = (a.get("href") or "").strip()
        rel = (a.get("rel") or "").lower()
        is_nofollow = "nofollow" in rel
        is_sponsored = "sponsored" in rel
        is_ugc = "ugc" in rel

        if is_nofollow:
            nofollow += 1
        if is_sponsored:
            sponsored_count += 1
        if is_ugc:
            ugc_count += 1

        # Track problematic href patterns (javascript:, empty, hash-only, missing)
        if not href or href == "#":
            empty_href_count += 1
            continue
        if href.startswith("javascript:"):
            javascript_href_count += 1
            continue
        if href.startswith("mailto:") or href.startswith("tel:"):
            continue
        if href.startswith("#"):
            continue

        anchor_text = _clean_anchor_text(a)

        absolute = _normalize_url(urljoin(page_url, href))
        link_domain = urlparse(absolute).netloc.lower()

        if link_domain == page_domain:
            internal += 1
            internal_urls.append(absolute)
            if is_nofollow:
                internal_nofollow += 1
            link_details.append(LinkDetail(
                url=absolute,
                anchor_text=anchor_text,
                is_nofollow=is_nofollow,
                link_type="internal",
            ))
        else:
            external += 1
            external_urls.append(absolute)
            # For external links in link_details, only store one per unique domain
            if link_domain not in seen_external_domains:
                seen_external_domains[link_domain] = absolute
                link_details.append(LinkDetail(
                    url=absolute,
                    anchor_text=anchor_text,
                    is_nofollow=is_nofollow,
                    link_type="external",
                ))

    if internal == 0:
        status = CheckStatus.WARNING
        note = "No internal links found"
    else:
        status = CheckStatus.PASS
        note = f"{internal} internal, {external} external, {nofollow} nofollow"

    return LinkStats(
        internal_count=internal,
        external_count=external,
        nofollow_count=nofollow,
        unique_internal_targets=len(set(internal_urls)),
        javascript_href_count=javascript_href_count,
        empty_href_count=empty_href_count,
        sponsored_count=sponsored_count,
        ugc_count=ugc_count,
        internal_nofollow_count=internal_nofollow,
        internal_urls=internal_urls,
        external_urls=external_urls,
        link_details=link_details,
        status=status,
        note=note,
    )


def check_open_graph(tree: HtmlElement) -> OpenGraphCheck:
    """
    Check Open Graph tags.
    Ported from seo-audit-mcp extractOpenGraph().
    """
    og_tags = {
        "og:title": None,
        "og:description": None,
        "og:image": None,
        "og:type": None,
        "og:url": None,
    }
    present: List[str] = []
    missing: List[str] = []

    for key in og_tags:
        vals = tree.xpath(f'//meta[@property="{key}"]/@content')
        if vals:
            og_tags[key] = vals[0].strip()
            present.append(key)
        else:
            missing.append(key)

    essential_missing = [t for t in missing if t in ("og:title", "og:description", "og:image")]
    if not present:
        status, note = CheckStatus.FAIL, "No Open Graph tags found"
    elif essential_missing:
        status, note = CheckStatus.WARNING, f"Missing: {', '.join(essential_missing)}"
    else:
        status, note = CheckStatus.PASS, "All essential OG tags present"

    return OpenGraphCheck(
        og_title=og_tags["og:title"],
        og_description=og_tags["og:description"],
        og_image=og_tags["og:image"],
        og_type=og_tags["og:type"],
        og_url=og_tags["og:url"],
        present_tags=present,
        missing_tags=missing,
        status=status,
        note=note,
    )


def check_twitter_card(tree: HtmlElement) -> TwitterCardCheck:
    """
    Check Twitter Card tags.
    Ported from seo-audit-mcp extractTwitterCard().
    """
    tc_tags = {
        "twitter:card": None,
        "twitter:title": None,
        "twitter:description": None,
        "twitter:image": None,
    }
    present: List[str] = []
    missing: List[str] = []

    for key in tc_tags:
        vals = tree.xpath(f'//meta[@name="{key}"]/@content')
        if vals:
            tc_tags[key] = vals[0].strip()
            present.append(key)
        else:
            missing.append(key)

    if not present:
        status, note = CheckStatus.INFO, "No Twitter Card tags found"
    elif "twitter:card" not in present:
        status, note = CheckStatus.WARNING, "Missing twitter:card type"
    else:
        status, note = CheckStatus.PASS, f"Twitter Card: {tc_tags['twitter:card']}"

    return TwitterCardCheck(
        card_type=tc_tags["twitter:card"],
        title=tc_tags["twitter:title"],
        description=tc_tags["twitter:description"],
        image=tc_tags["twitter:image"],
        present_tags=present,
        missing_tags=missing,
        status=status,
        note=note,
    )


def check_structured_data(tree: HtmlElement) -> StructuredDataCheck:
    """
    Extract and validate JSON-LD structured data.
    Ported from seo-audit-mcp extractJsonLd() + analyzeStructuredData().
    """
    items: List[StructuredDataItem] = []
    schema_types: List[str] = []
    has_json_ld = False

    scripts = tree.xpath('//script[@type="application/ld+json"]/text()')
    for script_text in scripts:
        try:
            data = json.loads(script_text.strip())
        except (json.JSONDecodeError, ValueError):
            items.append(
                StructuredDataItem(
                    schema_type="unknown",
                    errors=["Invalid JSON-LD: parse error"],
                )
            )
            has_json_ld = True
            continue

        has_json_ld = True
        # Handle @graph arrays
        entries = data.get("@graph", [data]) if isinstance(data, dict) else [data]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            schema_type = entry.get("@type", "unknown")
            if isinstance(schema_type, list):
                schema_type = ", ".join(schema_type)
            schema_types.append(schema_type)

            errors, warnings = _validate_structured_data(schema_type, entry)
            items.append(
                StructuredDataItem(
                    schema_type=schema_type,
                    properties=entry,
                    errors=errors,
                    warnings=warnings,
                )
            )

    total_errors = sum(len(i.errors) for i in items)
    if not has_json_ld:
        status, note = CheckStatus.INFO, "No JSON-LD structured data found"
    elif total_errors > 0:
        status, note = CheckStatus.WARNING, f"{total_errors} structured data errors"
    else:
        status, note = CheckStatus.PASS, f"Found: {', '.join(schema_types)}"

    return StructuredDataCheck(
        items=items,
        has_json_ld=has_json_ld,
        schema_types=schema_types,
        status=status,
        note=note,
    )


def _validate_structured_data(
    schema_type: str, data: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """Validate structured data entry. Returns (errors, warnings)."""
    errors: List[str] = []
    warnings: List[str] = []

    # Generic required field: @type must be valid
    if schema_type == "unknown":
        errors.append("Missing @type property")
        return errors, warnings

    if "JobPosting" in schema_type:
        required = ["title", "description", "datePosted", "hiringOrganization", "jobLocation"]
        for field in required:
            if field not in data:
                errors.append(f"JobPosting missing required field: {field}")
        recommended = ["validThrough", "baseSalary", "employmentType", "identifier", "directApply"]
        for field in recommended:
            if field not in data:
                warnings.append(f"JobPosting missing recommended field: {field}")
        valid_through = data.get("validThrough")
        if valid_through and isinstance(valid_through, str):
            try:
                from datetime import datetime
                exp = datetime.fromisoformat(valid_through.replace("Z", "+00:00"))
                if exp < datetime.now(exp.tzinfo):
                    errors.append("JobPosting has expired (validThrough in the past)")
            except (ValueError, TypeError):
                pass

    elif "Article" in schema_type or "NewsArticle" in schema_type or "BlogPosting" in schema_type:
        required = ["headline", "author", "datePublished"]
        for field in required:
            if field not in data:
                errors.append(f"{schema_type} missing required field: {field}")
        recommended = ["image", "dateModified", "publisher", "description"]
        for field in recommended:
            if field not in data:
                warnings.append(f"{schema_type} missing recommended field: {field}")

    elif "FAQPage" in schema_type:
        main_entity = data.get("mainEntity")
        if not main_entity:
            errors.append("FAQPage missing required 'mainEntity'")
        elif isinstance(main_entity, list):
            for i, item in enumerate(main_entity[:10]):
                if isinstance(item, dict):
                    if "name" not in item and "text" not in item:
                        errors.append(f"FAQPage question {i+1} missing 'name' (question text)")
                    accepted = item.get("acceptedAnswer")
                    if not accepted:
                        errors.append(f"FAQPage question {i+1} missing 'acceptedAnswer'")

    elif "Product" in schema_type:
        required = ["name"]
        for field in required:
            if field not in data:
                errors.append(f"Product missing required field: {field}")
        recommended = ["image", "description", "offers", "review", "aggregateRating", "brand"]
        for field in recommended:
            if field not in data:
                warnings.append(f"Product missing recommended field: {field}")

    elif "LocalBusiness" in schema_type or "Restaurant" in schema_type:
        required = ["name", "address"]
        for field in required:
            if field not in data:
                errors.append(f"{schema_type} missing required field: {field}")
        recommended = ["telephone", "openingHours", "image", "url", "geo"]
        for field in recommended:
            if field not in data:
                warnings.append(f"{schema_type} missing recommended field: {field}")

    elif "Event" in schema_type:
        required = ["name", "startDate", "location"]
        for field in required:
            if field not in data:
                errors.append(f"Event missing required field: {field}")
        recommended = ["endDate", "description", "image", "offers", "performer", "organizer"]
        for field in recommended:
            if field not in data:
                warnings.append(f"Event missing recommended field: {field}")

    elif "Recipe" in schema_type:
        required = ["name"]
        for field in required:
            if field not in data:
                errors.append(f"Recipe missing required field: {field}")
        recommended = ["image", "author", "prepTime", "cookTime", "recipeIngredient", "recipeInstructions"]
        for field in recommended:
            if field not in data:
                warnings.append(f"Recipe missing recommended field: {field}")

    elif "Organization" in schema_type:
        if "name" not in data:
            warnings.append("Organization missing 'name'")
        if "url" not in data:
            warnings.append("Organization missing 'url'")

    elif "BreadcrumbList" in schema_type:
        items = data.get("itemListElement", [])
        if not items:
            warnings.append("BreadcrumbList has no items")

    elif "WebSite" in schema_type:
        if "potentialAction" not in data:
            warnings.append("WebSite missing SearchAction (potentialAction)")

    elif "Person" in schema_type:
        if "name" not in data:
            warnings.append("Person missing 'name'")

    return errors, warnings


_VALID_REGION_CODES = frozenset({
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS",
    "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
    "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ",
    "EC", "EE", "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR",
    "GA", "GB", "GD", "GE", "GF", "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT",
    "GU", "GW", "GY", "HK", "HM", "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ",
    "IR", "IS", "IT", "JE", "JM", "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW",
    "KY", "KZ", "LA", "LB", "LC", "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY",
    "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK", "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS",
    "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA", "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP",
    "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG", "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT",
    "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW", "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI",
    "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS", "ST", "SV", "SX", "SY", "SZ",
    "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO", "TR", "TT", "TV", "TW", "TZ",
    "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI", "VN", "VU",
    "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW",
})

_VALID_LANG_CODES = frozenset({
    "aa", "ab", "af", "ak", "am", "an", "ar", "as", "av", "ay", "az",
    "ba", "be", "bg", "bh", "bi", "bm", "bn", "bo", "br", "bs",
    "ca", "ce", "ch", "co", "cr", "cs", "cu", "cv", "cy",
    "da", "de", "dv", "dz",
    "ee", "el", "en", "eo", "es", "et", "eu",
    "fa", "ff", "fi", "fj", "fo", "fr", "fy",
    "ga", "gd", "gl", "gn", "gu", "gv",
    "ha", "he", "hi", "ho", "hr", "ht", "hu", "hy", "hz",
    "ia", "id", "ie", "ig", "ii", "ik", "in", "io", "is", "it", "iu",
    "ja", "jv", "ka", "kg", "ki", "kj", "kk", "kl", "km", "kn",
    "ko", "kr", "ks", "ku", "kv", "kw", "ky",
    "la", "lb", "lg", "li", "ln", "lo", "lt", "lu", "lv",
    "mg", "mh", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my",
    "na", "nb", "nd", "ne", "ng", "nl", "nn", "no", "nr", "nv", "ny",
    "oc", "oj", "om", "or", "os",
    "pa", "pi", "pl", "ps", "pt",
    "qu",
    "rm", "rn", "ro", "ru", "rw",
    "sa", "sc", "sd", "se", "sg", "si", "sk", "sl", "sm", "sn", "so",
    "sq", "sr", "ss", "st", "su", "sv", "sw",
    "ta", "te", "tg", "th", "ti", "tk", "tl", "tn", "to", "tr", "ts", "tt", "tw", "ty",
    "ug", "uk", "ur", "uz",
    "ve", "vi", "vo",
    "wa", "wo",
    "xh",
    "yi", "yo",
    "za", "zh", "zu",
})


def check_hreflang(tree: HtmlElement) -> HreflangCheck:
    """Check hreflang tags for multilingual sites with language code validation."""
    entries: List[HreflangEntry] = []
    has_x_default = False
    validation_errors: List[str] = []

    for link in tree.xpath('//link[@rel="alternate"][@hreflang]'):
        lang = (link.get("hreflang") or "").strip()
        href = (link.get("href") or "").strip()
        if lang and href:
            entries.append(HreflangEntry(lang=lang, href=href))
            if lang == "x-default":
                has_x_default = True
            else:
                # Validate language code (e.g. "en", "en-US", "zh-Hans")
                parts = lang.split("-")
                primary = parts[0].lower()
                if primary not in _VALID_LANG_CODES:
                    validation_errors.append(f"Invalid language code: '{lang}'")
                elif len(parts) >= 2 and len(parts[1]) == 2 and parts[1].isalpha():
                    # BUG-18: Validate region subtag (ISO 3166-1 alpha-2)
                    region = parts[1].upper()
                    if region not in _VALID_REGION_CODES:
                        validation_errors.append(f"Invalid region code: '{lang}' ('{region}' is not a valid country)")

    if not entries:
        return HreflangCheck(status=CheckStatus.INFO, note="No hreflang tags (single language site)")

    if validation_errors:
        status = CheckStatus.WARNING
        note = f"{len(entries)} hreflang tags with {len(validation_errors)} invalid code(s): {'; '.join(validation_errors[:3])}"
    elif not has_x_default:
        status, note = CheckStatus.WARNING, f"{len(entries)} hreflang tags but missing x-default"
    else:
        status, note = CheckStatus.PASS, f"{len(entries)} hreflang tags with x-default"

    return HreflangCheck(
        entries=entries, has_x_default=has_x_default,
        validation_errors=validation_errors, status=status, note=note,
    )


def check_content(tree: HtmlElement) -> ContentCheck:
    """Check page content word count (flag thin content <300 words).

    Pages with forms (login, checkout, contact, etc.) use a lower threshold
    of 100 words since they are inherently transactional.
    """
    body = tree.xpath("//body")
    if not body:
        return ContentCheck(status=CheckStatus.WARNING, note="No body content found")

    # BUG-12: Remove script, style, noscript AND template elements (nav, header, footer)
    # to get accurate main-content word count
    body_copy = deepcopy(body[0])
    for tag in ("script", "style", "noscript", "nav", "header", "footer"):
        for el in body_copy.xpath(f".//{tag}"):
            el.getparent().remove(el)

    text = body_copy.text_content()
    words = len(text.split())

    # Lower threshold for form/transactional pages
    has_form = bool(tree.xpath("//form"))
    thin_threshold = 100 if has_form else 200

    if words < thin_threshold:
        label = f"Thin content ({words} words, <{thin_threshold})"
        if has_form:
            label += " — form page"
        status, note = CheckStatus.WARNING, label
    elif words < 600:
        status, note = CheckStatus.INFO, f"{words} words (consider expanding)"
    else:
        status, note = CheckStatus.PASS, f"{words} words"

    return ContentCheck(word_count=words, status=status, note=note)


def check_url_structure(url: str) -> URLCheck:
    """Check URL quality: length, special chars, hyphens vs underscores, case."""
    parsed = urlparse(url)
    path = parsed.path

    length = len(url)
    has_special = bool(re.search(r"[^a-zA-Z0-9/_\-.]", path))
    uses_hyphens = "-" in path
    has_underscores = "_" in path
    has_uppercase = path != path.lower()

    issues = []
    if length > 100:
        issues.append(f"URL too long ({length} chars)")
    if has_special:
        issues.append("Contains special characters")
    if has_underscores:
        issues.append("Uses underscores instead of hyphens")
    if has_uppercase:
        issues.append("Contains uppercase characters")

    if issues:
        status = CheckStatus.WARNING
        note = "; ".join(issues)
    else:
        status = CheckStatus.PASS
        note = "Clean URL structure"

    return URLCheck(
        url=url,
        length=length,
        has_special_chars=has_special,
        uses_hyphens=uses_hyphens,
        has_uppercase=has_uppercase,
        status=status,
        note=note,
    )


def check_mixed_content(tree: HtmlElement, page_url: str) -> MixedContentCheck:
    """
    Detect mixed content (HTTP resources on HTTPS pages).
    Ported from seo-audit-mcp checkMixedContent().
    """
    if not page_url.startswith("https://"):
        return MixedContentCheck(status=CheckStatus.PASS, note="Page not served over HTTPS")

    insecure: List[str] = []

    # Check common resource tags
    for tag, attr in [
        ("img", "src"),
        ("script", "src"),
        ("link", "href"),
        ("video", "src"),
        ("audio", "src"),
        ("source", "src"),
        ("iframe", "src"),
    ]:
        for el in tree.xpath(f"//{tag}[@{attr}]"):
            val = (el.get(attr) or "").strip()
            if val.startswith("http://"):
                insecure.append(val)

    if insecure:
        return MixedContentCheck(
            has_mixed_content=True,
            insecure_resources=insecure[:20],  # cap to avoid noise
            status=CheckStatus.WARNING,
            note=f"{len(insecure)} insecure resource(s) on HTTPS page",
        )
    return MixedContentCheck(status=CheckStatus.PASS, note="No mixed content detected")


def check_meta_refresh(tree: HtmlElement) -> Optional[str]:
    """Detect meta refresh redirects. Returns the redirect URL if found, else None."""
    refresh_tags = tree.xpath('//meta[@http-equiv="refresh"]/@content')
    if refresh_tags:
        content = refresh_tags[0].strip()
        # Parse "5;url=/target" or "0; url=https://example.com"
        # BUG-03: Don't lowercase the URL — only lowercase for the "url=" key detection
        lower = content.lower()
        if "url=" in lower:
            idx = lower.index("url=") + 4
            url_part = content[idx:].strip().strip("'\"")
            return url_part if url_part else None
        return content
    return None


def check_viewport(tree: HtmlElement) -> ViewportCheck:
    """Check viewport meta tag for mobile-friendliness."""
    viewports = tree.xpath('//meta[@name="viewport"]/@content')
    value = viewports[0].strip() if viewports else None
    if not value:
        return ViewportCheck(status=CheckStatus.FAIL, note="Missing viewport meta tag")
    return ViewportCheck(value=value, status=CheckStatus.PASS, note="Viewport configured")


def check_lang(tree: HtmlElement) -> LangCheck:
    """Check html lang attribute."""
    langs = tree.xpath("//html/@lang")
    value = langs[0].strip() if langs else None
    if not value:
        return LangCheck(status=CheckStatus.WARNING, note="Missing lang attribute on <html>")
    return LangCheck(value=value, status=CheckStatus.PASS, note=f"Language: {value}")


def check_charset(tree: HtmlElement) -> CharsetCheck:
    """Check charset declaration."""
    # <meta charset="utf-8">
    charsets = tree.xpath("//meta/@charset")
    if charsets:
        return CharsetCheck(
            value=charsets[0].strip(),
            status=CheckStatus.PASS,
            note=f"Charset: {charsets[0].strip()}",
        )
    # <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
    content_types = tree.xpath('//meta[@http-equiv="Content-Type"]/@content')
    if content_types:
        match = re.search(r"charset=([^\s;]+)", content_types[0])
        if match:
            return CharsetCheck(
                value=match.group(1),
                status=CheckStatus.PASS,
                note=f"Charset: {match.group(1)}",
            )
    return CharsetCheck(status=CheckStatus.WARNING, note="No charset declaration found")


def check_performance(
    tree: HtmlElement,
    raw_html: str,
    response_time_ms: Optional[float] = None,
    resource_breakdown: Optional[Dict[str, int]] = None,
) -> PerformanceCheck:
    """
    Performance check based on HTML content and optional resource timing data.
    When resource_breakdown is provided (from Performance Resource Timing API),
    page_weight_bytes reflects the real total transfer size across all resources.
    """
    html_weight = len(raw_html.encode("utf-8", errors="replace"))

    # Count resource tags that imply additional requests
    resource_selectors = [
        "//script[@src]",
        "//link[@rel='stylesheet']",
        "//img[@src]",
        "//video[@src]",
        "//audio[@src]",
        "//source[@src]",
        "//iframe[@src]",
    ]
    resource_count = sum(len(tree.xpath(sel)) for sel in resource_selectors)

    # Use real total page weight if resource breakdown is available
    if resource_breakdown:
        page_weight = sum(resource_breakdown.values())
    else:
        page_weight = html_weight

    issues = []
    if response_time_ms is not None and response_time_ms > 3000:
        issues.append(f"Slow response ({response_time_ms:.0f}ms)")
    if page_weight > 3 * 1024 * 1024:  # 3MB
        issues.append(f"Heavy page ({page_weight / 1024 / 1024:.1f}MB)")
    elif page_weight > 1 * 1024 * 1024:  # 1MB
        issues.append(f"Large page ({page_weight / 1024:.0f}KB)")
    if resource_count > 50:
        issues.append(f"Many resources ({resource_count})")

    if any("Slow" in i or "Heavy" in i for i in issues):
        status = CheckStatus.WARNING
    elif issues:
        status = CheckStatus.INFO
    else:
        status = CheckStatus.PASS

    if issues:
        note = "; ".join(issues)
    else:
        note = f"{page_weight / 1024:.0f}KB, {resource_count} resources"

    return PerformanceCheck(
        response_time_ms=response_time_ms,
        page_weight_bytes=page_weight,
        resource_count=resource_count,
        resource_breakdown=resource_breakdown,
        status=status,
        note=note,
    )


# ─── Main Per-Page Audit ──────────────────────────────────────────────


def check_hidden_text(tree: HtmlElement) -> List[str]:
    """Detect hidden text via inline style: display:none, visibility:hidden,
    white-on-white text color, and off-screen positioning (FN-11)."""
    hidden = []
    for el in tree.xpath('//*[@style]'):
        style_raw = (el.get("style") or "")
        style = style_raw.lower().replace(" ", "")
        text = (el.text_content() or "").strip()
        if not text:
            continue
        tag = el.tag
        snippet = text[:80]
        # Method 1 & 2: display:none, visibility:hidden
        if "display:none" in style or "visibility:hidden" in style:
            hidden.append(f"<{tag}> hidden via CSS: \"{snippet}\"")
        # Method 3: white-on-white or same color as background
        elif "color:" in style:
            import re as _re
            colors = _re.findall(r'(?:^|;|\s)color\s*:\s*([^;]+)', style_raw.lower())
            bg_colors = _re.findall(r'background(?:-color)?\s*:\s*([^;]+)', style_raw.lower())
            if colors and bg_colors:
                fg = colors[0].strip()
                bg = bg_colors[0].strip()
                if fg == bg or (fg in ("white", "#fff", "#ffffff", "rgb(255,255,255)") and bg in ("white", "#fff", "#ffffff", "rgb(255,255,255)")):
                    hidden.append(f"<{tag}> hidden via same text/background color: \"{snippet}\"")
            elif colors:
                fg = colors[0].strip()
                if fg in ("white", "#fff", "#ffffff", "rgb(255,255,255)"):
                    hidden.append(f"<{tag}> hidden via white text on default background: \"{snippet}\"")
        # Method 4: off-screen positioning
        if "position:" in style and any(off in style for off in ("left:-", "top:-", "left:-9999", "top:-9999")):
            if f"<{tag}>" not in str(hidden):  # avoid duplicate if already flagged
                hidden.append(f"<{tag}> hidden via off-screen positioning: \"{snippet}\"")
        elif "text-indent:" in style:
            import re as _re
            indent = _re.search(r'text-indent\s*:\s*(-\d+)', style_raw.lower())
            if indent and int(indent.group(1)) < -999:
                hidden.append(f"<{tag}> hidden via negative text-indent: \"{snippet}\"")
    return hidden


_STOP_WORDS = frozenset({
    # Articles, pronouns, prepositions, conjunctions
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "have", "been", "from", "this",
    "that", "with", "they", "will", "each", "make", "like", "just", "over",
    "such", "take", "than", "them", "very", "some", "could", "would", "into",
    # Additional common English words that cause false positives
    "page", "link", "links", "site", "web", "home", "menu", "main", "more",
    "about", "here", "also", "back", "when", "your", "what", "which", "their",
    "there", "where", "were", "been", "being", "other", "after", "before",
    "most", "only", "then", "first", "last", "next", "also", "does", "don",
    "how", "its", "let", "may", "new", "now", "old", "see", "way", "who",
    "did", "get", "got", "him", "his", "she", "too", "use", "used", "using",
    "these", "those", "through", "between", "should", "because", "while",
    "any", "every", "much", "many", "own", "same", "another", "know",
    "still", "well", "even", "come", "made", "find", "said", "say",
    # Common web/navigation words
    "click", "read", "view", "open", "close", "search", "content", "text",
    "title", "image", "images", "list", "item", "items", "data", "type",
    "name", "number", "info", "help", "contact", "email", "share", "follow",
    "post", "blog", "article", "tag", "tags", "category", "comment",
    "footer", "header", "navigation", "sidebar", "copyright", "privacy",
    "terms", "policy", "cookie", "cookies", "accept", "skip", "toggle",
})


def check_keyword_stuffing(tree: HtmlElement) -> Optional[str]:
    """Detect keyword stuffing by checking if any single word appears at >4% density with 15+ occurrences."""
    body = tree.xpath("//body")
    if not body:
        return None
    body_copy = deepcopy(body[0])
    for tag in ("script", "style", "noscript", "nav", "footer"):
        for el in body_copy.xpath(f".//{tag}"):
            el.getparent().remove(el)
    text = body_copy.text_content().lower()
    words = re.findall(r'\b[a-z]{3,}\b', text)  # min 3 chars to skip noise
    if len(words) < 50:
        return None
    counts = Counter(words)
    total = len(words)
    for word, count in counts.most_common(10):
        if word in _STOP_WORDS:
            continue
        density = count / total
        if density > 0.04 and count >= 15:
            return f"'{word}' appears {count} times ({density:.1%} density)"
    return None


_PLACEHOLDER_PATTERNS = [
    "lorem ipsum", "dolor sit amet", "consectetur adipiscing",
    "sed do eiusmod", "tempor incididunt", "ut labore et dolore",
]


def check_placeholder_content(tree: HtmlElement) -> bool:
    """Detect lorem ipsum / placeholder content in body text or meta description.
    Excludes navigation, anchor text, header, and footer to avoid false positives
    from link labels pointing to placeholder-content pages (BUG-9)."""
    body = tree.xpath("//body")
    text = ""
    if body:
        body_copy = deepcopy(body[0])
        for tag in ("nav", "header", "footer", "a"):
            for el in body_copy.xpath(f".//{tag}"):
                el.getparent().remove(el)
        text = body_copy.text_content().lower()
    desc = tree.xpath('//meta[@name="description"]/@content')
    if desc:
        text += " " + desc[0].lower()
    return any(p in text for p in _PLACEHOLDER_PATTERNS)


_SOFT_404_PATTERNS = re.compile(
    r"page\s*not\s*found|404\s*error|404\s*not\s*found|"
    r"not\s*found|doesn.?t\s*exist|no\s*longer\s*available|"
    r"we\s*couldn.?t\s*find|sorry.*page.*missing",
    re.IGNORECASE,
)


def _compute_content_hash(tree: HtmlElement) -> Optional[str]:
    """Compute a SHA-256 hash of normalized body text for duplicate content detection."""
    body = tree.xpath("//body")
    if not body:
        return None
    body_copy = deepcopy(body[0])
    for tag in ("script", "style", "noscript", "nav", "header", "footer"):
        for el in body_copy.xpath(f".//{tag}"):
            el.getparent().remove(el)
    text = " ".join(body_copy.text_content().lower().split())
    if len(text) < 50:
        return None
    return hashlib.sha256(text[:5000].encode("utf-8", errors="replace")).hexdigest()


def _compute_content_shingles(tree: HtmlElement, shingle_size: int = 5) -> Optional[frozenset]:
    """Compute a set of word-level shingles from body text for near-duplicate detection (BUG-8).
    Returns a frozenset of shingle hashes, or None if content is too short."""
    body = tree.xpath("//body")
    if not body:
        return None
    body_copy = deepcopy(body[0])
    for tag in ("script", "style", "noscript", "nav", "header", "footer"):
        for el in body_copy.xpath(f".//{tag}"):
            el.getparent().remove(el)
    words = body_copy.text_content().lower().split()
    if len(words) < 20:
        return None
    shingles = set()
    for i in range(len(words) - shingle_size + 1):
        shingle = " ".join(words[i:i + shingle_size])
        shingles.add(hashlib.md5(shingle.encode("utf-8", errors="replace")).hexdigest()[:8])
    return frozenset(shingles)


def _detect_soft_404(tree: HtmlElement, status_code: Optional[int]) -> bool:
    """Detect soft 404: page returns 200 but content indicates 'not found'."""
    if status_code and status_code != 200:
        return False
    # Check title (both text() and text_content() for robustness)
    for title_el in tree.xpath("//title"):
        title_text = (title_el.text_content() or "").strip()
        if title_text and _SOFT_404_PATTERNS.search(title_text):
            return True
    # Check H1
    for h1 in tree.xpath("//h1")[:3]:
        text = (h1.text_content() or "").strip()
        if text and _SOFT_404_PATTERNS.search(text):
            return True
    # Check body text (first 500 chars) for error-page patterns
    body = tree.xpath("//body")
    if body:
        body_text = (body[0].text_content() or "")[:500].strip()
        if _SOFT_404_PATTERNS.search(body_text):
            return True
    return False


def _count_iframes(tree: HtmlElement) -> tuple:
    """Count iframe elements and detect missing title / empty src (FN-12).
    Returns (total_count, missing_title_count, empty_src_count)."""
    iframes = tree.xpath("//iframe")
    missing_title = 0
    empty_src = 0
    for iframe in iframes:
        title = (iframe.get("title") or "").strip()
        src = (iframe.get("src") or "").strip()
        if not title:
            missing_title += 1
        if not src:
            empty_src += 1
    return len(iframes), missing_title, empty_src


def _check_pagination(tree: HtmlElement) -> Tuple[Optional[str], Optional[str]]:
    """Detect rel=next/prev pagination links. Returns (rel_next_url, rel_prev_url)."""
    rel_next = None
    rel_prev = None
    for link in tree.xpath('//link[@rel]'):
        rel = (link.get("rel") or "").lower()
        href = (link.get("href") or "").strip()
        if not href:
            continue
        if rel == "next":
            rel_next = href
        elif rel == "prev" or rel == "previous":
            rel_prev = href
    return rel_next, rel_prev


def audit_page(
    url: str,
    raw_html: str,
    status_code: Optional[int] = None,
    response_time_ms: Optional[float] = None,
    resource_breakdown: Optional[Dict[str, int]] = None,
) -> PageAuditResult:
    """
    Run all per-page SEO checks on the given HTML.

    Args:
        url: The page URL.
        raw_html: The full HTML content of the page.
        status_code: HTTP status code (if available).
        response_time_ms: Optional response time in milliseconds (TTFB).
        resource_breakdown: Optional dict of resource type -> bytes from Resource Timing API.

    Returns:
        PageAuditResult with all check results populated.
    """
    tree = _parse_html(raw_html)
    if tree is None:
        return PageAuditResult(
            url=url,
            status_code=status_code,
            title=TitleCheck(status=CheckStatus.FAIL, note="Could not parse HTML"),
        )

    rel_next, rel_prev = _check_pagination(tree)
    iframe_count, iframes_missing_title, iframes_empty_src = _count_iframes(tree)

    # FN-9: Detect lorem ipsum in meta description separately
    desc_vals = tree.xpath('//meta[@name="description"]/@content')
    has_placeholder_meta_desc = False
    if desc_vals:
        desc_lower = desc_vals[0].lower()
        has_placeholder_meta_desc = any(p in desc_lower for p in _PLACEHOLDER_PATTERNS)

    return PageAuditResult(
        url=url,
        status_code=status_code,
        title=check_title(tree),
        meta_description=check_meta_description(tree),
        canonical=check_canonical(tree, url),
        robots=check_robots_meta(tree),
        headings=check_headings(tree),
        images=check_images(tree),
        links=check_links(tree, url),
        open_graph=check_open_graph(tree),
        twitter_card=check_twitter_card(tree),
        structured_data=check_structured_data(tree),
        hreflang=check_hreflang(tree),
        content=check_content(tree),
        url_check=check_url_structure(url),
        mixed_content=check_mixed_content(tree, url),
        viewport=check_viewport(tree),
        lang=check_lang(tree),
        charset=check_charset(tree),
        performance=check_performance(tree, raw_html, response_time_ms, resource_breakdown),
        meta_refresh_url=check_meta_refresh(tree),
        hidden_text=check_hidden_text(tree),
        keyword_stuffing=check_keyword_stuffing(tree),
        has_placeholder_content=check_placeholder_content(tree),
        content_hash=_compute_content_hash(tree),
        content_shingles=_compute_content_shingles(tree),
        is_soft_404=_detect_soft_404(tree, status_code),
        iframe_count=iframe_count,
        iframes_missing_title=iframes_missing_title,
        iframes_empty_src=iframes_empty_src,
        has_placeholder_meta_desc=has_placeholder_meta_desc,
        rel_next=rel_next,
        rel_prev=rel_prev,
        inline_css_count=len(tree.xpath('//*[@style]')),
        inline_style_bytes=sum(len((el.text or '').encode()) for el in tree.xpath('//style')),
    )


# ─── Helpers ──────────────────────────────────────────────────────────


def _clean_anchor_text(a_element) -> str:
    """Extract clean text from an <a> element, stripping <style> and <script> content."""
    _SKIP_TAGS = {"style", "script"}
    parts = []
    if a_element.text:
        parts.append(a_element.text)
    for child in a_element:
        if child.tag in _SKIP_TAGS:
            # Skip the element entirely but keep its tail text
            if child.tail:
                parts.append(child.tail)
        else:
            parts.append(child.text_content() or "")
            if child.tail:
                parts.append(child.tail)
    text = " ".join("".join(parts).split())  # normalize whitespace
    return text[:200]


def _int_or_none(val: Optional[str]) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
