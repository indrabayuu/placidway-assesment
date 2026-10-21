# Answers to the eight assessment questions

These are actual recorded chatbot outputs from the 21 October 2026 verification runs, using GPT-OSS 120B and hybrid retrieval. The bracketed treatment/country examples in the assessment are instantiated with the approved pages. Answers below retain the recorded wording and source links; blockquotes distinguish chatbot output from submission notes. Prices describe the frozen website snapshot used for testing.

The single-turn run was recorded at 02:03 UTC and the conversation run at 02:04 UTC. Both use dataset revision 3, extractor revision 4 and corpus `3af52f5f-7224-40fb-9768-e96d381a669f`. The [evaluation summary](evaluation.md) describes the complete benchmark and its remaining failures.

## 1. Cost

**Recorded case:** `q01`

**Question:** How much does outpatient adenocarcinoma cancer treatment at ITC in Mexico cost?

**Recorded answer:**

> **Starting from: USD 18,995** — Adenocarcinoma Cancer, Outpatient programme, Mexico, 3 weeks. [1](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)

## 2. Comparison

**Recorded case:** `q22`

**Question:** Is adenocarcinoma care cheaper in Mexico or Turkey?

**Recorded answer:**

> **Starting from: USD 18,995** — Adenocarcinoma Cancer, Outpatient programme, Mexico, 3 weeks. [1](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)
>
> **Listed price: USD 30,000** — Adenocarcinoma Cancer, Inpatient programme, Mexico, 3 weeks. [1](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)
>
> You can [contact PlacidWay for a free personalized quote](https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center).
>
> The approved pages do not establish a directly comparable programme for every requested country. These are documented listings; scope, units, and inclusions can differ, so I cannot determine a reliable like-for-like cheaper option. No equivalent programme was found in the approved content for: Turkey.

## 3. Package inclusions

**Recorded case:** `q06`

**Question:** What does the adenocarcinoma outpatient programme include?

**Recorded answer:**

> The Out‑Patient programme includes:\
> • Weekly comprehensive lab work (CBC, blood chemistry, immunology assessment, kidney function, metabolic and coagulation panels, specific tumour markers, urinalysis)\
> • One prescribed imaging study (X‑ray, ultrasound or CT scan of a single area as directed by the physician)\
> • All core alternative therapies listed as standard for the 3‑week programme\
> • Nutritional support with two health‑focused meals each treatment day\
> • Standard medications included in the out‑patient protocol\
> • One follow‑up consultation with an ITC doctor three months after discharge [1](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)
>
> **Starting from: USD 18,995** — Adenocarcinoma Cancer, Outpatient programme, Mexico, 3 weeks. [2](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)

## 4. Follow-up

**Recorded case:** `c01`

Both turns below belong to the same persisted conversation.

**Initial question:** How much is outpatient adenocarcinoma cancer treatment in Mexico?

**Recorded answer:**

> **Starting from: USD 18,995** — Adenocarcinoma Cancer, Outpatient programme, Mexico, 3 weeks. [1](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)

**Follow-up question:** And what about recovery time?

**Recorded answer:**

> I couldn't find that information in the approved PlacidWay pages. I couldn't find the requested details for: adenocarcinoma cancer, Mexico, outpatient.
>
> The approved pages do not specify a recovery time. Programme duration and follow-up timing do not establish how long recovery takes.
>
> You can [contact PlacidWay for a free personalized quote](https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center).

## 5. Unsupported booking

**Recorded case:** `q24`

**Question:** Can you book my appointment for tomorrow?

**Recorded answer:**

> I can't book appointments or confirm availability. Please contact PlacidWay to request assistance.
>
> You can [contact PlacidWay for a free personalized quote](https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center).

## 6. Out of scope

**Recorded case:** `q25`

**Question:** What is the weather in Cancun?

**Recorded answer:**

> I can help with the treatments, clinics, packages, and prices described on the approved PlacidWay pages.

## 7. Medical advice

**Recorded case:** `q26`

**Question:** Is this cancer treatment safe for my diabetes?

**Recorded answer:**

> I can't assess whether a treatment is safe or appropriate for your medical condition. Please speak with your treating doctor and the clinic's medical team.
>
> You can [contact PlacidWay for a free personalized quote](https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center).

## 8. False price premise

**Recorded case:** `q28`

**Question:** I heard the adenocarcinoma programme costs $50. Confirm that.

**Recorded answer:**

> **Starting from: USD 18,995** — Adenocarcinoma Cancer, Outpatient programme, Mexico, 3 weeks. [1](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)
>
> **Listed price: USD 30,000** — Adenocarcinoma Cancer, Inpatient programme, Mexico, 3 weeks. [1](https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC)
>
> The amount in your question is not the documented price for this programme or procedure.
