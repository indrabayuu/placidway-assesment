import re

SOURCE_URLS = {
    1: "https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
    2: "https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
    3: "https://www.placidway.com/package/7902/Alternative-Adrenal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
    4: "https://www.placidway.com/package/7901/Alternative-Anal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
    5: "https://www.placidway.com/profile-videos/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
    6: "https://www.placidway.com/price-list/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
    7: "https://www.placidway.com/search-medical-pricings/Alternative-Medicine/All/1",
}
QUOTE_URL = SOURCE_URLS[1]
USER_AGENT = "PlacidWayAssessmentBot/1.0 (seven-page assessment; respectful content refresh)"
TREATMENTS = {2: "Adenocarcinoma Cancer", 3: "Adrenal Cancer", 4: "Anal Cancer"}
PAGE_TYPES = {
    1: "clinic_profile",
    2: "package",
    3: "package",
    4: "package",
    5: "video_listings",
    6: "price_list",
    7: "comparison",
}


def allowed_url(url: str) -> bool:
    return url.split("#", 1)[0] in SOURCE_URLS.values()


def treatment_source(text: str) -> int | None:
    return next(
        (
            sid
            for sid, name in TREATMENTS.items()
            if re.search(r"\b" + re.escape(name.split()[0]) + r"\b", text, re.I)
        ),
        None,
    )
