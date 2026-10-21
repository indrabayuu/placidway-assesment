import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from placidway.llm import CALLS, StructuredOutputError
from placidway.models import (
    Answer,
    AnswerBlock,
    AnswerDraft,
    Coverage,
    Evidence,
    PriceFact,
    ResolvedRequest,
    Verification,
)
from placidway.prompts import COVERAGE, DRAFT, RESOLVE, VERIFY
from placidway.sources import QUOTE_URL, TREATMENTS, allowed_url, treatment_source

TEXT = {
    "en": {
        "unknown": "I couldn't find that information in the approved PlacidWay pages.",
        "clarification": "Which treatment, country, or programme would you like information about?",
        "medical_referral": "I can't assess whether a treatment is safe or appropriate for your medical condition. Please speak with your treating doctor and the clinic's medical team.",
        "out_of_scope": "I can help with the treatments, clinics, packages, and prices described on the approved PlacidWay pages.",
        "unsupported_action": "I can't book appointments or confirm availability. Please contact PlacidWay to request assistance.",
        "quote": "You can [contact PlacidWay for a free personalized quote]({url}).",
        "disclaimer": "This is website information, not personal medical advice.",
        "from": "Starting from",
        "listed": "Listed price",
        "additional": "Additional charge",
        "outpatient": "Outpatient programme",
        "inpatient": "Inpatient programme",
        "duration": "3 weeks",
    },
    "es": {
        "unknown": "No encontré esa información en las páginas autorizadas de PlacidWay.",
        "clarification": "¿Sobre qué tratamiento, país o programa desea información?",
        "medical_referral": "No puedo determinar si un tratamiento es seguro o adecuado para su condición médica. Consulte a su médico tratante y al equipo médico de la clínica.",
        "out_of_scope": "Puedo ayudar con los tratamientos, clínicas, paquetes y precios descritos en las páginas autorizadas de PlacidWay.",
        "unsupported_action": "No puedo reservar citas ni confirmar disponibilidad. Contacte a PlacidWay para solicitar ayuda.",
        "quote": "Puede [contactar a PlacidWay para solicitar una cotización personalizada gratuita]({url}).",
        "disclaimer": "Esta información proviene del sitio web y no constituye asesoramiento médico personal.",
        "from": "Desde",
        "listed": "Precio publicado",
        "additional": "Cargo adicional",
        "outpatient": "Programa ambulatorio",
        "inpatient": "Programa de hospitalización",
        "duration": "3 semanas",
    },
    "ar": {
        "unknown": "لم أجد هذه المعلومات في صفحات PlacidWay المعتمدة.",
        "clarification": "ما العلاج أو البلد أو البرنامج الذي ترغب في معرفة المزيد عنه؟",
        "medical_referral": "لا يمكنني تحديد ما إذا كان العلاج آمنًا أو مناسبًا لحالتك الطبية. يرجى استشارة طبيبك المعالج والفريق الطبي في العيادة.",
        "out_of_scope": "يمكنني المساعدة في معلومات العلاجات والعيادات والباقات والأسعار الواردة في صفحات PlacidWay المعتمدة.",
        "unsupported_action": "لا يمكنني حجز المواعيد أو تأكيد توفرها. يرجى التواصل مع PlacidWay لطلب المساعدة.",
        "quote": "يمكنك [التواصل مع PlacidWay للحصول على عرض سعر شخصي مجاني]({url}).",
        "disclaimer": "هذه معلومات من الموقع وليست نصيحة طبية شخصية.",
        "from": "ابتداءً من",
        "listed": "السعر المنشور",
        "additional": "رسوم إضافية",
        "outpatient": "برنامج المرضى الخارجيين",
        "inpatient": "برنامج الإقامة الداخلية",
        "duration": "3 أسابيع",
    },
}


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    input: str
    response_language: str
    version: str
    request: dict
    evidence: list[dict]
    prices: list[dict]
    hits: list[dict]
    draft: dict
    deterministic_price: bool
    answer: dict
    disclaimer_shown: bool
    coverage: dict
    expanded: bool


def validate_draft(draft: AnswerDraft, evidence: list[Evidence], prices: list[PriceFact], request):
    ev = {e.id: e for e in evidence}
    facts = {f.id: f for f in prices}
    issues = []
    cited_support = "\n".join(
        ev[key].text
        for key in dict.fromkeys(key for block in draft.blocks for key in block.evidence_ids)
        if key in ev
    )
    requested = (request.query + " " + request.treatment).lower()
    cancer_source = treatment_source(requested)
    for block in draft.blocks:
        if block.kind == "text":
            if block.text and (not block.evidence_ids or any(key not in ev for key in block.evidence_ids)):
                issues.append("text_missing_support")
            if re.search(r"\$|€|£|\b(?:USD|EUR|MXN|dollars|dólares)\b", block.text, re.I):
                issues.append("monetary_text_must_use_price_record")
            for url in re.findall(r"https?://[^\s)<>\]]+", block.text):
                if not allowed_url(url.rstrip(".,")):
                    issues.append("unsupported_answer_url")
            if any(not allowed_url(url) for url in re.findall(r"\]\(([^)]+)\)", block.text)):
                issues.append("unsupported_answer_url")

            def numbers(value):
                value = re.sub(r"https?://\S+", "", value.lower())
                for index, word in enumerate(
                    (
                        "zero",
                        "one",
                        "two",
                        "three",
                        "four",
                        "five",
                        "six",
                        "seven",
                        "eight",
                        "nine",
                        "ten",
                        "eleven",
                        "twelve",
                    )
                ):
                    value = re.sub(r"\b" + word + r"\b", str(index), value)
                return {
                    Decimal(number.replace(",", "")) for number in re.findall(r"\d[\d,]*(?:\.\d+)?", value)
                }

            # References such as "beyond the one included scan" may depend on another
            # explicitly cited paragraph. Semantic verification still checks associations.
            if not numbers(block.text) <= numbers(cited_support):
                issues.append("unsupported_numeric_claim")
        elif block.price_id not in facts:
            issues.append("unknown_price_id")
        else:
            fact = facts[block.price_id]
            if cancer_source and fact.source_id != cancer_source:
                issues.append("wrong_treatment_price_source")
            if request.clinic:

                def clinic_tokens(value):
                    return set(re.findall(r"[a-z0-9]+", value.lower())) - {
                        "clinic",
                        "hospital",
                        "the",
                        "center",
                        "centre",
                    }

                if not clinic_tokens(request.clinic) <= clinic_tokens(fact.clinic):
                    issues.append("wrong_clinic")
            if not cancer_source and request.treatment:
                generic = {"cancer", "treatment", "alternative", "package", "programme", "program", "care"}
                requested_terms = set(re.findall(r"[a-z]+", request.treatment.lower())) - generic
                fact_terms = set(re.findall(r"[a-z]+", (fact.treatment + " " + fact.description).lower()))
                if not requested_terms <= fact_terms:
                    issues.append("wrong_treatment_price")
            programme = request.programme.lower().replace("-", "")
            if fact.kind in ("programme", "listing") and programme in ("outpatient", "inpatient"):
                if programme not in fact.programme.lower().replace("-", ""):
                    issues.append("wrong_programme")
            if (
                request.intent != "comparison"
                and request.country
                and request.country.lower() not in ("abroad", "any", "all")
            ):
                if fact.country.lower() != request.country.lower():
                    issues.append("wrong_country")
            if fact.currency != "USD" or fact.evidence_id not in ev:
                issues.append("price_missing_currency_or_evidence")
            if fact.amount is None and fact.qualifier != "varies":
                issues.append("price_missing_amount")
    if any(not allowed_url(e.url) for e in evidence):
        issues.append("source_url_outside_allowlist")
    return sorted(set(issues))


def render(draft, evidence, prices, language):
    words = TEXT[language]
    ev, facts = {e.id: e for e in evidence}, {f.id: f for f in prices}
    citations, numbers = [], {}

    def money(amount):
        return f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"

    def cite(keys):
        links = []
        for key in dict.fromkeys(keys):
            source = ev[key]
            if key not in numbers:
                numbers[key] = len(citations) + 1
                citations.append(
                    {"id": key, "url": source.url, "title": source.title, "section": source.heading}
                )
            links.append(f"[{numbers[key]}]({source.url})")
        return " ".join(links)

    pieces = []
    for block in draft.blocks:
        if block.kind == "text" and block.text:
            pieces.append(block.text.strip() + " " + cite(block.evidence_ids))
        elif block.kind == "price":
            fact = facts[block.price_id]
            amount = (
                {"en": "Price varies", "es": "Precio variable", "ar": "السعر متغير"}[language]
                if fact.amount is None
                else f"{fact.amount:,.0f}"
                if fact.amount == fact.amount.to_integral_value()
                else f"{fact.amount:,.2f}"
            )
            if fact.high is not None:
                amount += "–" + money(fact.high)
            label = (
                words["from"]
                if fact.qualifier in ("starting_from", "starting_estimate")
                else words["additional"]
                if fact.kind == "addon"
                else words["listed"]
            )
            if fact.qualifier == "starting_estimate":
                label = {
                    "en": "Estimated starting category range",
                    "es": "Rango inicial estimado de la categoría",
                    "ar": "النطاق التقديري الأولي للفئة",
                }[language]
            if fact.approximate or "approx" in fact.description.lower():
                label += {"en": " (approximately)", "es": " (aproximadamente)", "ar": " (تقريبًا)"}[language]
            if fact.kind == "addon":
                label += {
                    "en": " (excluded from base price)",
                    "es": " (no incluido en el precio base)",
                    "ar": " (غير مشمول في السعر الأساسي)",
                }[language]
            programme = (
                words["outpatient"]
                if fact.programme.startswith("Out-")
                else words["inpatient"]
                if fact.programme.startswith("In-")
                else fact.programme
            )
            if fact.kind == "addon":
                programme = ""
            scope = TREATMENTS.get(fact.source_id, "") if fact.kind == "addon" else ""
            context = ", ".join(
                x
                for x in (
                    scope,
                    fact.treatment,
                    programme,
                    fact.country,
                    words["duration"] if fact.duration.lower() == "3 weeks" else fact.duration,
                    fact.unit or ("per area" if "per area" in fact.description else ""),
                )
                if x
            )
            if fact.kind == "category_range":
                context += (
                    ". "
                    + {
                        "en": "Category estimate; not a quote for a specific cancer programme",
                        "es": "Estimación de categoría; no es una cotización para un programa oncológico específico",
                        "ar": "تقدير للفئة؛ وليس عرض سعر لبرنامج سرطان محدد",
                    }[language]
                )
            value = amount if fact.amount is None else fact.currency + " " + amount
            pieces.append(f"**{label}: {value}** — {context}. " + cite([fact.evidence_id]))
    return "\n\n".join(pieces), citations


class Workflow:
    def __init__(self, db, retriever, generator, checkpointer, pinned_version=None):
        self.db, self.retriever, self.generator, self.checkpointer = db, retriever, generator, checkpointer
        self.pinned_version = pinned_version

    def graph(self, stage=None, message_id="", created_at=""):
        repair_budget = [1]
        activity = []

        async def emit(name, message):
            item = {"stage": name, "message": message}
            activity.append(item)
            if stage:
                await stage(item)

        async def resolve(state):
            await emit("resolve", "Understanding your question and conversation context")
            history = [
                {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
                for m in state.get("messages", [])[-8:]
            ]
            request = await self.generator.call(
                ResolvedRequest,
                RESOLVE,
                {
                    "question": state["input"],
                    "history": history,
                    "response_language": state["response_language"],
                },
                repair_budget,
            )
            request.response_language = state["response_language"]
            user_context = (
                " ".join(item["content"] for item in history if item["role"] == "user") + " " + state["input"]
            )
            # A resolver may resolve references, but must not invent user constraints
            # from general knowledge (e.g. assuming Ayurveda means India/inpatient).
            if request.country and request.country.lower() not in user_context.lower():
                request.country = ""
            programme = request.programme.lower().replace("-", "")
            if programme in ("inpatient", "outpatient") and programme not in user_context.lower().replace(
                "-", ""
            ):
                request.programme = ""
            # Missing website prices need a quote fallback, even for unsupported treatments.
            if request.intent == "out_of_scope" and re.search(
                r"\bcost\b|\bprice\b|how much", state["input"], re.I
            ):
                request.intent = "cost"
            countries = [
                country
                for country in ("Mexico", "Turkey", "Thailand", "Croatia")
                if country.lower() in (state["input"] + " " + request.query).lower()
            ]
            if request.intent in ("general", "cost", "comparison") and (
                len(countries) > 1 or re.search(r"\bcheaper\b|\bversus\b|\bcompare\b", state["input"], re.I)
            ):
                request.intent = "comparison"
            # Factual website questions about published rates or coordination are not requests for diagnosis.
            if request.intent == "medical_referral" and re.search(
                r"survival rate|published.*rate|coordinate|work with.*oncologist", state["input"], re.I
            ):
                request.intent = "general"
            if (
                request.intent == "medical_referral"
                and re.search(r"video|testimonial", state["input"], re.I)
                and not re.search(r"\bmy\b|\bI\b|\bme\b", state["input"])
            ):
                request.intent = "general"
            if re.search(r"guarantee.*(?:remission|cure|cancer)", state["input"], re.I):
                request.intent = "medical_referral"
            if request.intent == "clarification" and re.search(
                r"(?:unnamed|blank).*row", state["input"], re.I
            ):
                request.intent = "general"
            named_source = treatment_source(state["input"])
            if (
                request.intent == "clarification"
                and named_source
                and re.search(
                    r"\b(?:free|cost|price|include\w*|duration|recovery|surgery)\b", state["input"], re.I
                )
            ):
                request.treatment = TREATMENTS[named_source]
                request.intent = "inclusions" if re.search(r"includ", state["input"], re.I) else "general"
                request.query = state["input"]
            return {"request": request.model_dump()}

        async def retrieve(state):
            await emit("retrieve", "Finding relevant source passages and price records")
            request = ResolvedRequest.model_validate(state["request"])
            evidence, prices, hits = await self.retriever.retrieve(
                request, state["version"], self.db.config.retrieval_mode
            )
            return {
                "evidence": [e.model_dump() for e in evidence],
                "prices": [f.model_dump(mode="json") for f in prices],
                "hits": [{**h, "score": float(h["score"])} for h in hits],
            }

        async def coverage(state):
            await emit("coverage", "Checking whether the requested facts are covered")
            result = await self.generator.call(
                Coverage,
                COVERAGE,
                {
                    "question": state["input"],
                    "request": state["request"],
                    "evidence": state["evidence"],
                    "price_facts": state["prices"],
                },
                repair_budget,
            )
            return {"coverage": result.model_dump()}

        async def broaden(state):
            await emit("retrieve", "Checking broader source passages for missing facts")
            request = ResolvedRequest.model_validate(state["request"])
            evidence, prices, hits = await self.retriever.retrieve(
                request, state["version"], self.db.config.retrieval_mode, expanded=True
            )
            return {
                "expanded": True,
                "evidence": [e.model_dump() for e in evidence],
                "prices": [fact.model_dump(mode="json") for fact in prices],
                "hits": [{**hit, "score": float(hit["score"])} for hit in hits],
            }

        async def draft(state):
            await emit("draft", "Preparing an answer from the approved evidence")
            request = ResolvedRequest.model_validate(state["request"])
            additional_prices = []
            # Exact factual price lookups need selection and validation, not price generation.
            if request.intent in ("cost", "comparison", "general", "inclusions"):
                generic = {
                    "cancer",
                    "treatment",
                    "alternative",
                    "programme",
                    "program",
                    "care",
                    "package",
                    "procedure",
                    "external",
                    "additional",
                    "cost",
                    "price",
                }

                def words(value):
                    return {
                        word[:-1] if len(word) > 3 and word.endswith("s") else word
                        for word in re.findall(r"[a-z]+", value.lower().replace("tumour", "tumor"))
                    }

                tokens = words(request.treatment) - generic
                scope = treatment_source(state["input"] + " " + request.query)
                subject = (
                    words(state["input"])
                    - generic
                    - {
                        "how",
                        "much",
                        "do",
                        "doe",
                        "is",
                        "are",
                        "the",
                        "an",
                        "a",
                        "in",
                        "at",
                        "for",
                        "of",
                        "and",
                        "or",
                        "itc",
                        "mexico",
                        "turkey",
                        "thailand",
                        "croatia",
                        "adenocarcinoma",
                        "adrenal",
                        "anal",
                        "what",
                        "from",
                        "to",
                        "extra",
                        "included",
                        "include",
                        "excluded",
                        "with",
                        "on",
                        "my",
                        "your",
                        "this",
                        "that",
                        "these",
                        "those",
                        "all",
                        "every",
                        "one",
                        "can",
                        "about",
                        "after",
                        "before",
                        "when",
                        "post",
                        "discharge",
                        "follow",
                        "up",
                    }
                )
                addon_scores = {
                    fact["id"]: len(subject & words(fact["treatment"] + " " + fact["description"]))
                    for fact in state["prices"]
                    if fact["kind"] == "addon" and (not scope or fact["source_id"] == scope)
                }
                best_addon = max(addon_scores.values(), default=0)
                subject_scores = {
                    fact["id"]: len(subject & (words(fact["treatment"]) - generic))
                    for fact in state["prices"]
                }
                best_subject = max(subject_scores.values(), default=0)
                chosen = []
                for fact in state["prices"]:
                    fact_tokens = words(fact["treatment"] + " " + fact["description"])
                    if best_addon and addon_scores.get(fact["id"], 0) != best_addon:
                        continue
                    if (
                        not best_addon
                        and not tokens
                        and (best_subject < 2 or subject_scores.get(fact["id"]) != best_subject)
                    ):
                        continue
                    if not best_addon and tokens and not tokens <= fact_tokens:
                        continue
                    if scope and fact["source_id"] != scope:
                        continue
                    if (
                        request.intent != "comparison"
                        and request.country
                        and request.country.lower() not in (fact["country"].lower(), "abroad")
                    ):
                        continue
                    programme = request.programme.lower().replace("-", "")
                    if (
                        programme in ("inpatient", "outpatient")
                        and fact["kind"] == "programme"
                        and programme not in fact["programme"].lower().replace("-", "")
                    ):
                        continue
                    chosen.append(fact)
                if request.intent == "inclusions" and best_addon:
                    additional_prices = chosen
                    excluded = {
                        section["id"]: section
                        for section in state["evidence"]
                        if re.search(r"excluded services|optional specialized", section["heading"], re.I)
                    }
                    ambiguous_imaging = any(
                        "imaging" in fact["treatment"].lower() for fact in chosen
                    ) and not re.search(r"additional|extra|supplemental|beyond", state["input"], re.I)
                    if (
                        chosen
                        and not ambiguous_imaging
                        and not re.search(r"\bfree\b", state["input"], re.I)
                        and all(fact["evidence_id"] in excluded for fact in chosen)
                    ):
                        statement = {
                            "en": "The requested service is listed as an excluded or optional additional charge and is not included in the base programme.",
                            "es": "El servicio solicitado figura como un cargo adicional excluido u opcional y no está incluido en el programa base.",
                            "ar": "الخدمة المطلوبة مدرجة كرسوم إضافية مستثناة أو اختيارية وليست مشمولة في البرنامج الأساسي.",
                        }[state["response_language"]]
                        return {
                            "deterministic_price": True,
                            "draft": AnswerDraft(
                                outcome="partial"
                                if any(fact["amount"] is None for fact in chosen)
                                else "answered",
                                blocks=[
                                    AnswerBlock(
                                        kind="text",
                                        text=statement,
                                        evidence_ids=list(
                                            dict.fromkeys(fact["evidence_id"] for fact in chosen)
                                        ),
                                    ),
                                    *[AnswerBlock(kind="price", price_id=fact["id"]) for fact in chosen],
                                ],
                                offer_quote=any(fact["amount"] is None for fact in chosen),
                            ).model_dump(),
                        }
                exact_lookup = request.intent in ("cost", "comparison") or (
                    request.intent == "general"
                    and best_addon > 0
                    and best_subject >= 2
                    and re.search(r"\blist(?:s|ed)?\b|\bprice\b|\bcost\b|\bcharge\b", state["input"], re.I)
                )
                mixed_question = not best_addon and re.search(r"\bfree\b|\binclud", state["input"], re.I)
                surgery_exclusion = next(
                    (
                        section
                        for section in state["evidence"]
                        if (not scope or section["source_id"] == scope)
                        and "excluded" in section["heading"].lower()
                        and re.search(r"major surg", section["text"], re.I)
                    ),
                    None,
                )
                if (
                    chosen
                    and scope
                    and re.search(r"\bfree\b", state["input"], re.I)
                    and re.search(r"surgery|surgical", state["input"], re.I)
                    and surgery_exclusion
                ):
                    statement = {
                        "en": "This is not a free programme. Major surgery is listed among excluded services and is not included in the base programme.",
                        "es": "Este programa no es gratuito. La cirugía mayor figura entre los servicios excluidos y no está incluida en el programa base.",
                        "ar": "هذا البرنامج ليس مجانيًا. الجراحة الكبرى مدرجة ضمن الخدمات المستثناة وليست مشمولة في البرنامج الأساسي.",
                    }[state["response_language"]]
                    return {
                        "deterministic_price": True,
                        "draft": AnswerDraft(
                            outcome="answered",
                            blocks=[
                                *[AnswerBlock(kind="price", price_id=fact["id"]) for fact in chosen],
                                AnswerBlock(
                                    kind="text", text=statement, evidence_ids=[surgery_exclusion["id"]]
                                ),
                            ],
                        ).model_dump(),
                    }
                if (
                    chosen
                    and exact_lookup
                    and not mixed_question
                    and len({fact["source_id"] for fact in chosen}) == 1
                ):
                    if request.intent == "comparison":
                        requested_countries = {
                            country
                            for country in ("Mexico", "Turkey", "Thailand", "Croatia")
                            if country.lower() in (state["input"] + " " + request.query).lower()
                        }
                        scoped = [fact for fact in chosen if fact["country"] in requested_countries]
                        if scoped:
                            chosen = scoped
                    return {
                        "deterministic_price": True,
                        "draft": AnswerDraft(
                            outcome="partial"
                            if request.intent == "comparison"
                            or any(fact["amount"] is None for fact in chosen)
                            else "answered",
                            blocks=[AnswerBlock(kind="price", price_id=fact["id"]) for fact in chosen],
                            offer_quote=any(fact["amount"] is None for fact in chosen),
                        ).model_dump(),
                    }
            # Compact model-facing references avoid transcription errors in long content hashes.
            references = {f"E{i}": section["id"] for i, section in enumerate(state["evidence"], 1)}
            reverse = {value: key for key, value in references.items()}
            presented = [{**section, "id": reverse[section["id"]]} for section in state["evidence"]]
            prices = [
                {
                    **fact,
                    "evidence_id": reverse[fact["evidence_id"]],
                    "context_evidence_ids": [
                        reverse[key] for key in fact.get("context_evidence_ids", []) if key in reverse
                    ],
                }
                for fact in state["prices"]
            ]
            value = await self.generator.call(
                AnswerDraft,
                DRAFT,
                {
                    "question": state["input"],
                    "request": state["request"],
                    "response_language": state["response_language"],
                    "evidence": presented,
                    "price_facts": prices,
                    "coverage": state.get("coverage", {}),
                },
                repair_budget,
            )
            for block in value.blocks:
                price_refs = {fact["id"]: fact["evidence_id"] for fact in state["prices"]}
                block.evidence_ids = list(
                    dict.fromkeys(references.get(key, price_refs.get(key, key)) for key in block.evidence_ids)
                )
            if re.search(r"(?:unnamed|blank).*row", state["input"], re.I) and value.outcome == "answered":
                value.outcome, value.offer_quote = "partial", True
            if value.outcome in ("answered", "partial"):
                for fact in additional_prices:
                    if not any(block.price_id == fact["id"] for block in value.blocks):
                        value.blocks.append(AnswerBlock(kind="price", price_id=fact["id"]))
            return {"draft": value.model_dump()}

        async def validate(state):
            await emit("validate", "Checking price context, citations, and factual support")
            candidate = AnswerDraft.model_validate(state["draft"])
            evidence = [Evidence.model_validate(e) for e in state["evidence"]]
            prices = [PriceFact.model_validate(f) for f in state["prices"]]
            request = ResolvedRequest.model_validate(state["request"])
            issues = validate_draft(candidate, evidence, prices, request)
            text, citations = (
                ("", []) if issues else render(candidate, evidence, prices, state["response_language"])
            )
            if issues:
                candidate = AnswerDraft(outcome="unknown", offer_quote=True)
                text, citations = "", []
            words = TEXT[state["response_language"]]
            if not text:
                text = words.get(candidate.outcome, words["unknown"])
                details = ", ".join(
                    value
                    for value in (request.treatment, request.country, request.programme, request.clinic)
                    if value
                )
                if details and candidate.outcome == "unknown":
                    text += (
                        " "
                        + {
                            "en": "I couldn't find the requested details for: ",
                            "es": "No encontré los detalles solicitados para: ",
                            "ar": "لم أجد التفاصيل المطلوبة بخصوص: ",
                        }[state["response_language"]]
                        + details
                        + "."
                    )

            def extra_citation(section):
                existing = next(
                    (index for index, row in enumerate(citations, 1) if row["id"] == section.id), None
                )
                if existing is None:
                    citations.append(
                        {
                            "id": section.id,
                            "url": section.url,
                            "title": section.title,
                            "section": section.heading,
                        }
                    )
                    existing = len(citations)
                return f" [{existing}]({section.url})"

            if candidate.outcome in ("unknown", "partial") and re.search(
                r"recovery", state["input"] + " " + request.query, re.I
            ):
                text += (
                    "\n\n"
                    + {
                        "en": "The approved pages do not specify a recovery time. Programme duration and follow-up timing do not establish how long recovery takes.",
                        "es": "Las páginas autorizadas no especifican el tiempo de recuperación. La duración del programa y el momento del seguimiento no determinan cuánto tarda la recuperación.",
                        "ar": "لا تحدد الصفحات المعتمدة مدة التعافي. مدة البرنامج وموعد المتابعة لا يحددان المدة اللازمة للتعافي.",
                    }[state["response_language"]]
                )
            if (
                candidate.outcome in ("unknown", "partial")
                and re.search(r"recovery", state["input"], re.I)
                and re.search(r"(?:3|three)\s+months", state["input"], re.I)
                and any(re.search(r"(?:3|three)\s+months", section.text, re.I) for section in evidence)
            ):
                matching = next(
                    section for section in evidence if re.search(r"(?:3|three)\s+months", section.text, re.I)
                )
                text += (
                    " "
                    + {
                        "en": "Three months refers to the follow-up consultation, not a recovery estimate.",
                        "es": "Los tres meses se refieren a la consulta de seguimiento, no a una estimación de recuperación.",
                        "ar": "تشير الأشهر الثلاثة إلى استشارة المتابعة، وليس إلى تقدير مدة التعافي.",
                    }[state["response_language"]]
                    + extra_citation(matching)
                )
            if candidate.offer_quote or candidate.outcome in ("unknown", "partial"):
                text += "\n\n" + words["quote"].format(url=QUOTE_URL)
            if candidate.blocks and re.search(r"typical.*includ|includ.*typical", state["input"], re.I):
                varying = next(
                    (
                        section
                        for section in evidence
                        if "exact clinic package can still vary" in section.text.lower()
                    ),
                    None,
                )
                if varying:
                    text += (
                        "\n\n"
                        + {
                            "en": "The exact clinic package can vary; confirm the inclusions in writing.",
                            "es": "El paquete exacto puede variar según la clínica; confirme las inclusiones por escrito.",
                            "ar": "قد تختلف الباقة المحددة حسب العيادة؛ يرجى تأكيد المشمولات كتابيًا.",
                        }[state["response_language"]]
                        + extra_citation(varying)
                    )
            if candidate.blocks and state.get("deterministic_price", False):
                from placidway.ingestion import parse_money

                quoted = parse_money(state["input"])
                selected = [
                    fact for fact in prices if fact.id in {block.price_id for block in candidate.blocks}
                ]
                if quoted and not any(quoted[0] == fact.amount for fact in selected):
                    text += (
                        "\n\n"
                        + {
                            "en": "The amount in your question is not the documented price for this programme or procedure.",
                            "es": "El importe de su pregunta no es el precio documentado de este programa o procedimiento.",
                            "ar": "المبلغ المذكور في سؤالك ليس السعر الموثق لهذا البرنامج أو الإجراء.",
                        }[state["response_language"]]
                    )
            if (
                request.intent == "comparison"
                and candidate.blocks
                and all(block.kind == "price" for block in candidate.blocks)
            ):
                text += (
                    "\n\n"
                    + {
                        "en": "The approved pages do not establish a directly comparable programme for every requested country. These are documented listings; scope, units, and inclusions can differ, so I cannot determine a reliable like-for-like cheaper option.",
                        "es": "Las páginas autorizadas no establecen un programa directamente comparable para todos los países solicitados. Estos son precios publicados; el alcance, las unidades y las inclusiones pueden diferir, por lo que no puedo determinar qué opción equivalente es más económica.",
                        "ar": "لا تثبت الصفحات المعتمدة وجود برنامج قابل للمقارنة مباشرة في كل بلد مطلوب. هذه أسعار منشورة وقد يختلف النطاق والوحدات والمشمولات، لذلك لا يمكنني تحديد الخيار الأرخص على أساس مقارنة متكافئة.",
                    }[state["response_language"]]
                )
                documented = {
                    fact.country
                    for fact in prices
                    if fact.id in {block.price_id for block in candidate.blocks}
                }
                missing = [
                    country
                    for country in ("Mexico", "Turkey", "Thailand", "Croatia")
                    if country.lower() in (state["input"] + " " + request.query).lower()
                    and country not in documented
                ]
                if missing:
                    text += (
                        " "
                        + {
                            "en": "No equivalent programme was found in the approved content for: ",
                            "es": "No se encontró un programa equivalente en el contenido autorizado para: ",
                            "ar": "لم يتم العثور على برنامج مكافئ في المحتوى المعتمد للبلدان التالية: ",
                        }[state["response_language"]]
                        + ", ".join(missing)
                        + "."
                    )
            disclaimer = candidate.medical_disclaimer and not state.get("disclaimer_shown", False)
            if disclaimer:
                text += "\n\n" + words["disclaimer"]
            # Verify the final rendered candidate, including price-premise corrections
            # and comparison limitations, before any message is checkpointed or streamed.
            if not issues and candidate.blocks:
                verification = await self.generator.call(
                    Verification,
                    VERIFY,
                    {
                        "question": state["input"],
                        "response_language": state["response_language"],
                        "candidate": text,
                        "outcome": candidate.outcome,
                        "evidence": state["evidence"],
                        "price_facts": state["prices"],
                    },
                    repair_budget,
                )
                if not verification.supported:
                    issues = verification.issues or ["semantic_support_failed"]
                    candidate = AnswerDraft(outcome="unknown", offer_quote=True)
                    text = words["unknown"]
                    details = ", ".join(
                        value
                        for value in (request.treatment, request.country, request.programme, request.clinic)
                        if value
                    )
                    if details:
                        text += (
                            " "
                            + {
                                "en": "I couldn't find the requested details for: ",
                                "es": "No encontré los detalles solicitados para: ",
                                "ar": "لم أجد التفاصيل المطلوبة بخصوص: ",
                            }[state["response_language"]]
                            + details
                            + "."
                        )
                    text += "\n\n" + words["quote"].format(url=QUOTE_URL)
                    citations, disclaimer = [], False
            result = Answer(
                content=text,
                outcome=candidate.outcome,
                response_language=state["response_language"],
                citations=citations,
                evidence_ids=[c["id"] for c in citations],
                version=state["version"],
                diagnostics={
                    "validation_issues": issues,
                    "draft": state["draft"],
                    "accepted_draft": candidate.model_dump(),
                    "used_price_ids": [b.price_id for b in candidate.blocks if b.kind == "price"],
                    "retrieval_hits": state["hits"],
                    "request": state["request"],
                    "evidence": state["evidence"],
                    "prices": state["prices"],
                    "coverage": state.get("coverage", {}),
                    "expanded_retrieval": state.get("expanded", False),
                },
            )
            return {
                "answer": result.model_dump(),
                "disclaimer_shown": state.get("disclaimer_shown", False) or disclaimer,
            }

        async def boundary(state):
            outcome = state["request"]["intent"]
            words = TEXT[state["response_language"]]
            if outcome == "unsupported_action" and re.search(r"invent|ignore.*rules", state["input"], re.I):
                outcome = "unknown"
            text = words[outcome]
            if outcome == "medical_referral" and re.search(r"guarantee", state["input"], re.I):
                text = {
                    "en": "I cannot guarantee remission or cure from testimonials. Please discuss individual prognosis and treatment suitability with your treating doctor and the clinic's medical team.",
                    "es": "No puedo garantizar la remisión ni la curación a partir de testimonios. Consulte el pronóstico individual y la idoneidad del tratamiento con su médico y el equipo de la clínica.",
                    "ar": "لا يمكنني ضمان الشفاء أو زوال المرض استنادًا إلى الشهادات. يرجى مناقشة التوقعات الفردية وملاءمة العلاج مع طبيبك وفريق العيادة الطبي.",
                }[state["response_language"]]
            if outcome == "unsupported_action" and re.search(r"send|records|pay", state["input"], re.I):
                text = {
                    "en": "I can't send medical records or process payments. Please contact PlacidWay or the clinic directly for assistance.",
                    "es": "No puedo enviar expedientes médicos ni procesar pagos. Contacte directamente con PlacidWay o la clínica para obtener ayuda.",
                    "ar": "لا يمكنني إرسال السجلات الطبية أو معالجة المدفوعات. يرجى التواصل مباشرة مع PlacidWay أو العيادة للمساعدة.",
                }[state["response_language"]]
            if outcome == "out_of_scope" and re.search(
                r"exchange rate|convert|currency", state["input"], re.I
            ):
                text += (
                    " "
                    + {
                        "en": "I don't have live exchange rates and cannot provide a currency conversion from these sources.",
                        "es": "No dispongo de tipos de cambio en tiempo real y no puedo convertir divisas con estas fuentes.",
                        "ar": "لا تتوفر لدي أسعار صرف مباشرة ولا يمكنني تحويل العملات استنادًا إلى هذه المصادر.",
                    }[state["response_language"]]
                )
            if outcome in ("medical_referral", "unsupported_action", "unknown"):
                text += "\n\n" + words["quote"].format(url=QUOTE_URL)
            return {
                "answer": Answer(
                    content=text,
                    outcome=outcome,
                    response_language=state["response_language"],
                    version=state["version"],
                ).model_dump()
            }

        async def finalize(state):
            answer = state["answer"]
            metadata = {
                "created_at": created_at or datetime.now(timezone.utc).isoformat(),
                "citations": answer["citations"],
                "activity": activity,
                "outcome": answer["outcome"],
                "response_language": state["response_language"],
            }
            return {
                "messages": [
                    AIMessage(content=answer["content"], id=message_id or None, additional_kwargs=metadata)
                ]
            }

        graph = StateGraph(State)
        for name, node in (
            ("resolve", resolve),
            ("retrieve", retrieve),
            ("coverage", coverage),
            ("broaden", broaden),
            ("draft", draft),
            ("validate", validate),
            ("boundary", boundary),
            ("finalize", finalize),
        ):
            graph.add_node(name, node)
        graph.add_edge(START, "resolve")
        graph.add_conditional_edges(
            "resolve",
            lambda s: (
                "boundary"
                if s["request"]["intent"]
                in ("medical_referral", "out_of_scope", "unsupported_action", "clarification")
                else "retrieve"
            ),
        )
        graph.add_edge("retrieve", "coverage")
        graph.add_conditional_edges(
            "coverage",
            lambda s: "broaden" if not s["coverage"]["sufficient"] and not s.get("expanded") else "draft",
        )
        graph.add_edge("broaden", "coverage")
        graph.add_edge("draft", "validate")
        graph.add_edge("validate", "finalize")
        graph.add_edge("boundary", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=self.checkpointer)

    async def run(self, conversation_id, question, language, message_id, run_id, stage=None):
        version = self.pinned_version or await self.db.active_version()
        if not version:
            raise RuntimeError("Knowledge base not initialized")
        calls = []
        token = CALLS.set(calls)
        started = time.monotonic()
        created = datetime.now(timezone.utc).isoformat()
        try:
            result = await self.graph(stage, run_id, created).ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=question, id=message_id, additional_kwargs={"created_at": created}
                        )
                    ],
                    "input": question,
                    "response_language": language,
                    "version": version,
                    "request": {},
                    "evidence": [],
                    "prices": [],
                    "hits": [],
                    "draft": {},
                    "deterministic_price": False,
                    "answer": {},
                    "coverage": {},
                    "expanded": False,
                },
                {"configurable": {"thread_id": str(conversation_id)}, "recursion_limit": 12},
            )
            result["answer"]["diagnostics"].update(
                {"calls": calls, "seconds": round(time.monotonic() - started, 3)}
            )
            last = result["messages"][-1]
            message = {
                "id": str(last.id),
                "role": "assistant",
                "content": last.content,
                **last.additional_kwargs,
            }
            return result["answer"], message
        except StructuredOutputError:
            text = (
                {
                    "en": "I couldn't produce a validated answer to this question.",
                    "es": "No pude generar una respuesta validada a esta pregunta.",
                    "ar": "لم أتمكن من تقديم إجابة تم التحقق منها لهذا السؤال.",
                }[language]
                + "\n\n"
                + TEXT[language]["quote"].format(url=QUOTE_URL)
            )
            metadata = {
                "created_at": created,
                "response_language": language,
                "citations": [],
                "activity": [],
                "outcome": "unknown",
            }
            answer = Answer(
                content=text,
                outcome="unknown",
                response_language=language,
                version=version,
                diagnostics={
                    "validation_issues": ["structured_output_invalid"],
                    "calls": calls,
                    "seconds": round(time.monotonic() - started, 3),
                },
            ).model_dump()
            await self.graph().aupdate_state(
                {"configurable": {"thread_id": str(conversation_id)}},
                {
                    "answer": answer,
                    "messages": [AIMessage(content=text, id=run_id, additional_kwargs=metadata)],
                },
                as_node="finalize",
            )
            return answer, {"id": run_id, "role": "assistant", "content": text, **metadata}
        finally:
            CALLS.reset(token)

    async def history(self, conversation_id):
        state = await self.graph().aget_state({"configurable": {"thread_id": str(conversation_id)}})
        items = []
        for message in state.values.get("messages", []):
            if isinstance(message, (HumanMessage, AIMessage)):
                items.append(
                    {
                        "id": str(message.id),
                        "role": "user" if isinstance(message, HumanMessage) else "assistant",
                        "content": message.content,
                        "citations": [],
                        "activity": [],
                        **message.additional_kwargs,
                    }
                )
        return items
