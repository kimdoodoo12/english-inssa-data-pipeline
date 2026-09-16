"""
enrich_dataset.py

final_dataset.jsonl을 학습 데이터로 보강:
1. Definition patches  — bet/sus/goat 등 Wiktionary 오류 정의 수동 수정
2. difficulty_tier     — rank 기반 학습 우선순위 (essential/common/supplemental)
3. Example validation  — 단어가 실제로 등장하지 않는 reddit_example 제거

실행 후 curated_dataset, auto_approved, needs_review 파일도 재생성.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FINAL_PATH    = Path("data_pipeline/output/final_dataset.jsonl")
CURATED_PATH  = Path("data_pipeline/output/curated_dataset.jsonl")
AUTO_PATH     = Path("data_pipeline/output/auto_approved.jsonl")
REVIEW_PATH   = Path("data_pipeline/output/needs_review.jsonl")

# ── 1. Definition patches ────────────────────────────────────────────────────
# Wiktionary에서 파싱된 정의가 슬랭 의미를 빠뜨린 단어들.
# def_source_label, slang_tier도 함께 수정.
DEFINITION_PATCHES: dict[str, dict] = {
    # ── 의미 자체가 틀린 단어 ───────────────────────────────────────────────────
    "goat": {
        "definition_en": "Greatest Of All Time; used to describe someone as the absolute best at something",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "bet": {
        "definition_en": "Expression of agreement or affirmation; 'okay', 'sure', or 'for sure'",
        "def_source_label": ["slang", "internet slang"],
        "slang_tier": "pure_slang",
    },
    "sus": {
        "definition_en": "Suspicious; behaving in a sketchy or untrustworthy way",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "stoned": {
        # Wiktionary 정의가 "intoxicated by alcohol"로 틀림 — 실제는 대마초
        "definition_en": "Intoxicated by marijuana or cannabis; high",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "insane": {
        # Wiktionary 정의가 "furious" (분노)로 틀림 — 실제 슬랭 = "대박/미쳤다"
        "definition_en": "Incredibly impressive, extreme, or wild; used to express amazement",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "def": {
        # Wiktionary 정의 "; very good" 는 부정확 — 실제 슬랭 = "definitely"
        "definition_en": "Definitely; for certain (also used as adjective meaning 'cool' or 'excellent')",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "sad": {
        # Wiktionary 정의 "bah!" 는 완전히 무관 — 인터넷 슬랭 = "불쌍한/한심한"
        "definition_en": "Pathetic, lame, or pitiful; used to dismiss someone or something as hopeless",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },

    # ── 파싱 깨짐 (구두점만 남음) ─────────────────────────────────────────────
    "gotta": {
        "definition_en": "Have got to; must (informal contraction of 'got to')",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "lmfao": {
        "definition_en": "Laughing My F***ing Ass Off; expressing intense amusement",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "kk": {
        "definition_en": "Okay, okay; casual affirmative response, slightly warmer than 'ok'",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "ftfy": {
        "definition_en": "Fixed That For You; used when humorously correcting or editing someone's statement",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },

    # ── 앞부분 잘림 (정의 앞 구문 누락) ───────────────────────────────────────
    "omg": {
        "definition_en": "Oh My God; exclamation of surprise, shock, excitement, or disbelief",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "hmu": {
        "definition_en": "Hit Me Up; please contact or message me",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "y'all": {
        "definition_en": "You all; informal plural second-person pronoun addressing a group",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "welp": {
        "definition_en": "Well; an exclamation expressing resignation or rueful acceptance of an awkward situation",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "phat": {
        "definition_en": "Cool, excellent, or attractive; originally AAVE slang, popularized in hip-hop",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "uwu": {
        "definition_en": "An emoticon expressing warmth, cuteness, or affection; used in online and anime communities",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "bruv": {
        "definition_en": "Brother; British slang term of address for a friend or mate",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "ganja": {
        "definition_en": "Marijuana; cannabis (originally from Jamaican English, widely used in slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "nuff": {
        "definition_en": "Enough; informal shortening, often used emphatically as in 'nuff said'",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "yee": {
        "definition_en": "Exclamation of excitement or enthusiasm; also used in 'yee yee' to mock redneck stereotypes",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },

    # ── rank 1~300 추가 패치 (형태는 멀쩡하지만 의미 오류/누락) ───────────────
    "drip": {
        # Wiktionary 정의가 "to whine/complain" (영국 영어) — 실제 Gen Z 슬랭 = 패션/스타일
        "definition_en": "Stylish or fashionable clothing and accessories; to have great personal style",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "ghost": {
        # "; absent" 은 slang 의미(잠수타기)를 빠뜨림
        "definition_en": "To suddenly cut off all contact with someone and ignore them completely, especially in a dating context",
        "def_source_label": ["slang", "internet slang"],
        "slang_tier": "pure_slang",
    },
    "extra": {
        # "To an extraordinary degree" 는 부정확 — 실제 슬랭 = 오버하는/과장된
        "definition_en": "Over-the-top, unnecessarily dramatic, or doing too much; excessively showy",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "ngl": {
        # ": to tell the truth" → 앞부분 잘림
        "definition_en": "Not Gonna Lie; used before an honest or blunt admission",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "lit": {
        # ", fantastic; cool" → 앞부분 잘림
        "definition_en": "Exciting, fun, or excellent; full of energy and enjoyment",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "hard": {
        # "Sexually aroused" 는 일부 슬랭 의미지만 음악/퀄리티 의미 누락
        "definition_en": "Excellent or impressive (especially in music); also: sexually aroused",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "slay": {
        # "Something excellent" 은 명사 정의 — 실제 사용은 동사
        "definition_en": "To perform, look, or do something with exceptional style and excellence; to absolutely nail it",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 전수 조사 결과 추가 패치 (rank 순)
    # ══════════════════════════════════════════════════════════════════════════

    # ── 의미 자체가 틀린 단어들 ────────────────────────────────────────────────
    "crazy": {
        # "Eccentric behaviour; lunacy" 는 명사형 — 실제 슬랭 = 부사 강조어
        "definition_en": "Very; extremely (intensifier); also, amazing, unbelievable, or mentally unstable",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "stupid": {
        # "The condition of being stupid" 는 명사형 — 실제 슬랭 = 강조 부사
        "definition_en": "Extremely; very (intensifier); also, foolish or senseless",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "never": {
        # "Negative particle" 는 너무 좁음 — 실제 슬랭 = 불신/충격의 감탄
        "definition_en": "Not at any time; also used as an exclamation of disbelief or shock ('She would never!')",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "shot": {
        # "A home run" 은 야구 특수 의미 — 실제 슬랭 = 시도/술
        "definition_en": "An attempt or try; also a measure of spirits; also, a remark or criticism aimed at someone",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "suck": {
        # "A short drink of spirits" — 실제 슬랭 = 형편없다
        "definition_en": "To be bad, disappointing, or of poor quality ('this movie sucks')",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "push": {
        # "To approach an age" — 실제 슬랭 = 마약 팔다 / 적극 홍보
        "definition_en": "To sell drugs; to aggressively promote or market something",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "throw": {
        # "A single instance" — 실제 슬랭 = 일부러 지다 / 당황시키다
        "definition_en": "To deliberately lose a competition; also, to confuse or catch someone off guard",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "beast": {
        # "Cadet's basic training at West Point" — 실제 슬랭 = 최강자
        "definition_en": "An exceptionally skilled, strong, or impressive person; to beast = to dominate or perform at an extraordinary level",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "loser": {
        # "A person convicted of a crime" — 실제 슬랭 = 루저/실패자
        "definition_en": "A person regarded as a failure or someone who is pathetic, uncool, or unworthy of respect",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "drag": {
        # "A systematic search over a wide area" — 실제 슬랭 = 드래그 퀸 / 지루한 것
        "definition_en": "Cross-dressing or performing in women's clothing (as in drag queen); also, something tedious or boring",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "dirty": {
        # "Served with olive juice" 는 칵테일 특수 의미 — 실제 슬랭 = 성적/불공정
        "definition_en": "Sexually explicit or risqué; also, unfair or underhanded ('that's dirty')",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "af": {
        # "A native African in Rhodesia" — 완전히 틀림, 실제 = as fuck
        "definition_en": "As fuck; used as a slang intensifier (e.g., 'tired af', 'cute af')",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "whore": {
        # "To overuse something" — 실제 슬랭 = 문란한 사람 / 욕설
        "definition_en": "A promiscuous person or sex worker; also used as a general insult",
        "def_source_label": ["slang", "vulgar"],
        "slang_tier": "pure_slang",
    },
    "ditch": {
        # "The city of Calcutta" — 완전히 틀림
        "definition_en": "To abandon, leave behind, or skip; to ditch class = to skip school",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "pov": {
        # "A poor person; a pauper" — 완전히 틀림
        "definition_en": "Point of view; used in social media to describe a first-person perspective video or scenario",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "hella": {
        # "For sure; hell yeah" — 강조 확언이 아닌 부사 강조어
        "definition_en": "Very; a lot; extremely (Bay Area/California slang, now widespread)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "burnout": {
        # "Someone whose brains burned out by drugs" — 현대 주용법 누락
        "definition_en": "A state of physical and mental exhaustion from prolonged stress, especially from work or responsibilities",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "fucked": {
        # "Bothered to do something" — 완전히 틀림
        "definition_en": "In serious trouble; broken or ruined; also, sexually penetrated (vulgar)",
        "def_source_label": ["slang", "vulgar"],
        "slang_tier": "pure_slang",
    },
    "blasting": {
        # "Administering full dosage of PEDs" — 실제 슬랭 = 크게 음악 틀기 / 비판
        "definition_en": "Playing music at high volume; also, criticizing harshly; shooting rapidly",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "sneaky": {
        # "Any device for covert surveillance" — 실제 슬랭 = 몰래 하는
        "definition_en": "Acting in a sly, secretive, or underhanded manner; doing something on the sly",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "fodder": {
        # "Tracing paper" — 완전히 틀림
        "definition_en": "Someone or something used as expendable material; easy targets to defeat (from 'cannon fodder')",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "groyper": {
        # "A large green cartoon toad" — 틀림 (Pepe the Frog 혼동)
        "definition_en": "A far-right internet troll or provocateur; a follower associated with white nationalist ideology online",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "bloody": {
        # "Bloody mary" — 칵테일 의미 — 실제 슬랭 = 영국 강조어
        "definition_en": "A British intensifier expressing anger or emphasis ('bloody hell', 'bloody brilliant')",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "sandbox": {
        # "The Middle East" — 실제 슬랭 = 테스트 환경 / 게임 오픈월드
        "definition_en": "A safe testing environment; also, an open-world game mode with no fixed objectives",
        "def_source_label": ["slang", "internet slang"],
        "slang_tier": "pure_slang",
    },
    "jackass": {
        # "A kind of bootleg liquor" — 실제 슬랭 = 멍청이/무례한 사람
        "definition_en": "A stupid, foolish, or obnoxious person; an idiot or jerk",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "boner": {
        # "A blunder; a silly mistake" — 구식 의미; 현대 슬랭 = 발기
        "definition_en": "A penile erection (vulgar slang)",
        "def_source_label": ["slang", "vulgar"],
        "slang_tier": "pure_slang",
    },
    "bounced": {
        # "Unsuccessful delivery of email" — 실제 슬랭 = 자리를 뜨다
        "definition_en": "Left; departed; also, to have been removed or kicked out",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "dog shit": {
        # "(sometimes shortened to dog)" — 파싱 깨짐
        "definition_en": "Terrible; of very poor quality; awful",
        "def_source_label": ["slang", "vulgar"],
        "slang_tier": "pure_slang",
    },
    "bounce back": {
        # "Of an email, to be returned to sender" — 이메일 특수 의미
        "definition_en": "To recover quickly from a setback, illness, or failure",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "swinger": {
        # "Anything very large, forcible, or astonishing" — 실제 슬랭 = 파트너 스왑
        "definition_en": "A person who engages in consensual partner-swapping or group sexual activity",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "snatched": {
        # "Narrow" — 완전히 틀림
        "definition_en": "Stylish and on point; looking great; also, having a well-defined, slim figure",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "bbc": {
        # 축구 선수 설명 — 틀림
        "definition_en": "Big black cock; a sexual slang term",
        "def_source_label": ["slang", "vulgar"],
        "slang_tier": "pure_slang",
    },
    "gassed": {
        # "Exhausted" — 틀림; 영국 슬랭 = 신난/들뜬
        "definition_en": "Excited; enthusiastic; hyped up (British slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "mb": {
        # "Moneyboy" — 틀림; 실제 = my bad
        "definition_en": "My bad; an apology for a mistake",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "defo": {
        # "Defamation" — 완전히 틀림
        "definition_en": "Definitely (British/Australian informal abbreviation)",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "stalk": {
        # "The penis" — 틀림; 실제 슬랭 = 누군가를 집착적으로 따라다니다
        "definition_en": "To obsessively follow or monitor someone, especially online; to stalk someone's profile",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "bingo": {
        # "Brandy" — 실제 슬랭 = 정확해!/맞아!
        "definition_en": "Exactly right; correct; used as an exclamation when something is spot on",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "wavy": {
        # "Drunk" — 실제 Gen Z 슬랭 = 스타일리시/쿨한
        "definition_en": "Stylish; cool; fashionable (hip-hop and Gen Z slang)",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "fool": {
        # "Foolish" — 실제 AAVE 슬랭 = 친구/사람에 대한 호칭
        "definition_en": "A friend or person; used as a term of address (especially in Black and Latino slang)",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "lad": {
        # "A scammer" — 실제 슬랭 = 젊은 남자 / 유쾌한 남자
        "definition_en": "A young man; a boy or guy; also used in British culture to describe boisterous, typically male behavior",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "totes": {
        # "Legit" — 완전히 틀림
        "definition_en": "Totally; completely (informal abbreviation)",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "goblin": {
        # "A railway line in north London" — 틀림; 슬랭 = 탐욕스럽고 게으른 행동
        "definition_en": "A greedy or mischievous person; goblin mode = acting in an unrestrained, messy, self-indulgent way",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "red herring": {
        # "A soldier" — 완전히 틀림
        "definition_en": "A misleading clue or distraction that draws attention away from the real issue",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "sharp": {
        # "An expert" — 부정확; 실제 슬랭 = 옷 잘 입는/날카로운
        "definition_en": "Stylish; well-dressed; looking impressively put-together",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "sprung": {
        # "Drunk" — 실제 슬랭 = 누군가에게 푹 빠진
        "definition_en": "Completely infatuated with someone; head over heels for someone",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "bottled": {
        # "Drunk" — 영국 슬랭에서는 = 겁먹고 도망친
        "definition_en": "Lost one's nerve; chickened out at the last moment (British slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "crit": {
        # "Criticism" — 게임 슬랭 = 크리티컬 히트
        "definition_en": "Critical hit; a powerful strike in gaming that deals bonus damage",
        "def_source_label": ["slang", "internet slang"],
        "slang_tier": "pure_slang",
    },
    "x": {
        # "Christ" — 문자 메시지 슬랭 = 키스 / 엑스터시
        "definition_en": "A kiss (used in text messages); also, the drug ecstasy/MDMA",
        "def_source_label": ["slang", "internet slang"],
        "slang_tier": "pure_slang",
    },
    "stinks": {
        # "Chemistry" — 완전히 틀림
        "definition_en": "To be terrible or of very poor quality ('this stinks')",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "funk": {
        # "Fuck" — 틀림; 실제 = 나쁜 기분 / 나쁜 냄새
        "definition_en": "A state of depression or low energy; also, a strong unpleasant smell; in a funk = in a bad mood",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "deez nuts": {
        # "These nuts" — 너무 직역; 실제 = 특정 밈/농담의 펀치라인
        "definition_en": "These testicles; used as a punchline in the 'deez nuts' joke format (internet meme)",
        "def_source_label": ["internet slang", "vulgar"],
        "slang_tier": "pure_slang",
    },
    "whooped": {
        # "Flawless" — 완전히 틀림
        "definition_en": "Exhausted; very tired; also, having been decisively defeated",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "spiral": {
        # "A helix" — 기하학적 정의; 실제 슬랭 = 급격히 나빠지다
        "definition_en": "A rapid deterioration or downward slide; to spiral = to get progressively worse out of control",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "mum": {
        # "Silent" — 실제 영국 슬랭 = 엄마
        "definition_en": "Mother (British English informal)",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "go out": {
        # "To fail" — 실제 슬랭 = 외출하다 / 사귀다
        "definition_en": "To go to a bar, party, or social event; also, to be in a romantic relationship with someone",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "raisin": {
        # "Guardian" — 완전히 틀림
        "definition_en": "An old or wrinkled person (derogatory slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "clock out": {
        # "To die" — 실제 슬랭 = 퇴근하다 (비유적으로 종료)
        "definition_en": "To finish one's shift at work; to stop working for the day; also used figuratively to mean giving up",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "crab": {
        # "To ruin" — 실제 = 불평하다 / 게잡이
        "definition_en": "To complain or find fault; to crab about something; also, pubic lice (vulgar slang)",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "jive": {
        # ": patent nonsense" → 앞 잘림
        "definition_en": "Nonsense or deceptive talk; to jive = to be consistent or make sense",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "gagged": {
        # "; stunned" → 앞 잘림
        "definition_en": "Stunned or overwhelmed with amazement or shock (drag/LGBTQ+ slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "snoop": {
        # "To steal" — 실제 = 몰래 염탐하다
        "definition_en": "To pry or look around sneakily; to investigate someone's private affairs without permission",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "biscuit": {
        # "The head" — 틀림; 미국 슬랭 = 총기
        "definition_en": "A gun; a firearm (American slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "poo": {
        # "Champagne" — 실제 = 대변 (유아어)
        "definition_en": "Feces; excrement (childish or informal)",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "mick": {
        # "Easy" — 아일랜드인 비하 또는 "piece of cake" 의미
        "definition_en": "An Irish person (often offensive); also used informally to mean something easy",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "donny": {
        # "Doncaster" — 지명 설명; 실제 슬랭 = 사람/남자
        "definition_en": "A person; a man or boy (British slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "bol": {
        # "Bolognese" — 틀림; 실제 슬랭 = bro/brother
        "definition_en": "Brother; bro (informal term of address)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "robo": {
        # "A robot" — 실제 슬랭 = 기침약으로 취하기
        "definition_en": "Robotripping; getting high on cough medicine containing DXM",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },

    # ── 앞부분 잘린 정의 (rank 순 보완) ────────────────────────────────────────
    "nsfw": {
        "definition_en": "Not safe for work; content containing adult, graphic, or inappropriate material",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "ya": {
        "definition_en": "Yeah; yes (informal affirmation)",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "sauce": {
        "definition_en": "Source; used when asking for the origin of an image or piece of media",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "asf": {
        # "Age, sex, location" 는 asl의 정의 — asf = as fuck
        "definition_en": "As fuck; used as a slang intensifier (e.g., 'weird asf', 'funny asf')",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "op": {
        "definition_en": "Original poster; the person who started a discussion thread or forum post",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "peeps": {
        "definition_en": "People; one's friends or close associates",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "you got it": {
        "definition_en": "That's correct; understood; used to confirm agreement or acknowledge a request",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "ftw": {
        # ": a biker slogan" 는 오래된 의미; 현재 = for the win
        "definition_en": "For the win; used to express enthusiasm, support, or endorsement",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "blue": {
        "definition_en": "Sad; melancholic; feeling unhappy or depressed",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "fully": {
        "definition_en": "Actually; really; completely (used as an intensifier in British slang)",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "crack": {
        "definition_en": "A highly addictive form of cocaine smoked in small rocks; also, something excellent or amusing ('good crack')",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "sc": {
        "definition_en": "Screenshot; a captured image of one's screen; also, Snapchat",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "ham": {
        "definition_en": "Going all-out with maximum effort and intensity; to go ham = to go hard or overdo it",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "rofl": {
        "definition_en": "Rolling on the floor laughing; used to express intense amusement",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "high": {
        "definition_en": "Intoxicated by drugs, especially cannabis; experiencing the effects of recreational drug use",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "for real": {
        "definition_en": "Truly; seriously; used for emphasis or to confirm sincerity ('are you for real?')",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "feels": {
        "definition_en": "Strong emotions or feelings; an intense sentimental or emotional reaction",
        "def_source_label": ["internet slang", "slang"],
        "slang_tier": "pure_slang",
    },
    "smol": {
        "definition_en": "Small and cute (internet slang, used affectionately)",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "jank": {
        "definition_en": "Of poor quality, broken, or unreliable; janky (gamer/internet slang)",
        "def_source_label": ["slang", "internet slang"],
        "slang_tier": "pure_slang",
    },
    "gat": {
        "definition_en": "A gun; a firearm (slang)",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "no biggie": {
        "definition_en": "Not important; not something to worry about; no big deal",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "crickets": {
        "definition_en": "Absolute silence; no response; used to describe being completely ignored",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "psyched": {
        "definition_en": "Excited; enthusiastic; pumped up and ready",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "copy that": {
        "definition_en": "Understood; message received (borrowed from radio communication; used to confirm)",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "perc": {
        "definition_en": "A Percocet or oxycodone pill, especially used recreationally",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "to god": {
        "definition_en": "Very much; truly; I swear to God (used for emphasis or to affirm something)",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "digits": {
        "definition_en": "A phone number; especially someone's contact number for romantic purposes",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "antsy": {
        "definition_en": "Restless, impatient, and unable to keep still; fidgety with anxiety or excitement",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "blitzed": {
        "definition_en": "Very drunk; wasted",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "knocked up": {
        "definition_en": "Pregnant, typically unexpectedly or outside of marriage",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "sort of": {
        "definition_en": "Somewhat; to some extent but not entirely; kind of",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "on the real": {
        "definition_en": "Seriously; honestly; for real",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "homeboy": {
        "definition_en": "A close friend, especially one from the same neighborhood or background",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
    "dark web": {
        "definition_en": "The encrypted, hidden part of the internet not indexed by search engines, often associated with illegal activity",
        "def_source_label": ["internet slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "oxy": {
        "definition_en": "OxyContin; an opioid painkiller often abused as a recreational drug",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "digging": {
        "definition_en": "Liking or enjoying something; being into it ('I'm really digging this song')",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "steamed": {
        "definition_en": "Angry; very upset; hot under the collar",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "toque": {
        "definition_en": "A beanie or knitted winter hat (Canadian slang)",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "emo": {
        "definition_en": "A subculture associated with emotional, dark aesthetics and music; a person in this subculture; emotionally sensitive",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "fu": {
        "definition_en": "Fuck you (online abbreviation used as an insult)",
        "def_source_label": ["internet slang", "vulgar"],
        "slang_tier": "pure_slang",
    },
    "flak": {
        "definition_en": "Heavy criticism or hostile opposition",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "start up": {
        "definition_en": "A newly established business, especially in the tech industry",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "sunshine and rainbows": {
        "definition_en": "Positive, optimistic things; often used sarcastically to describe unrealistic expectations",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "rolling": {
        "definition_en": "Intoxicated from MDMA/ecstasy; also, staggering drunk",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "a whole nother": {
        "definition_en": "An entirely different (thing); a whole other matter entirely",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "hundo": {
        "definition_en": "A hundred; a $100 bill",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "nam": {
        "definition_en": "Vietnam (informal); The Nam = the Vietnam War era",
        "def_source_label": ["slang"],
        "slang_tier": "pure_slang",
    },
    "panning": {
        "definition_en": "Harsh criticism; panning a film or performance means reviewing it very negatively",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "informal_slang",
    },
    "tell the truth": {
        "definition_en": "To be honest; to disclose the facts honestly",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "tell you the truth": {
        "definition_en": "To be honest with you; used as a discourse marker meaning 'honestly speaking'",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "sleep around": {
        "definition_en": "To have sexual relations with numerous partners",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "pricey": {
        "definition_en": "Expensive; costing a lot of money",
        "def_source_label": ["colloquial"],
        "slang_tier": "informal_slang",
    },
    "generational": {
        "definition_en": "The best of a generation; generational talent = a once-in-a-generation level of skill",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },

    # ── 파이프라인 완료 후 발견된 오류 수정 ────────────────────────────────────
    "wtf": {
        # Wiktionary 정의 "A bafflingly bad or unwise design decision" — 완전히 틀림
        "definition_en": "What the fuck; an exclamation of shock, disbelief, or frustration",
        "def_source_label": ["internet slang"],
        "slang_tier": "pure_slang",
    },
    "fuck": {
        # Wiktionary 정의 "To be very good, to rule, go hard" — 단일 의미로 틀림
        "definition_en": "A versatile exclamation or intensifier expressing strong emotion, frustration, or emphasis",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "eat it": {
        # Stage 9.5가 Reddit 성적 예문에 과적합 → "To engage in sexual activity" (틀린 교정)
        "definition_en": "To fall hard or crash; to wipe out spectacularly (also used figuratively for any bad failure)",
        "def_source_label": ["slang", "colloquial"],
        "slang_tier": "pure_slang",
    },
    "cap": {
        # Stage 6에서 drop (60개 컨텍스트 전부 market cap/salary cap) → 수동 복구
        "definition_en": "To lie or exaggerate; often used in 'no cap' meaning 'for real, no lie'",
        "def_source_label": ["slang", "aave"],
        "slang_tier": "pure_slang",
    },
}

# ── 2. Difficulty tiers ───────────────────────────────────────────────────────
TIER_ESSENTIAL_MAX = 200   # 학습 앱 필수 1순위
TIER_COMMON_MAX    = 700   # 자주 접하는 슬랭


def difficulty_tier(rank: int) -> str:
    if rank <= TIER_ESSENTIAL_MAX:
        return "essential"
    if rank <= TIER_COMMON_MAX:
        return "common"
    return "supplemental"


# ── 3. Example validation ─────────────────────────────────────────────────────
def _word_in_text(word: str, text: str) -> bool:
    """word(또는 그 stem)가 text에 실제로 등장하는지 확인.

    - 단어 경계(\\b) 검사로 'fire' in 'fire-fighter' 오매칭 방지
    - 다중어('low key')는 공백 포함 서브스트링 검사
    - 대소문자 무시
    """
    w = word.lower()
    t = text.lower()
    if " " in w:
        return w in t
    pattern = r"\b" + re.escape(w) + r"\w*\b"  # stem match: vibe→vibing
    return bool(re.search(pattern, t))


def validate_examples(word: str, examples: list[str]) -> list[str]:
    """단어가 등장하지 않는 예문 제거. 모두 실패하면 원본 유지."""
    valid = [ex for ex in examples if _word_in_text(word, ex)]
    return valid if valid else examples


# ── Split / curated criteria (split_for_review.py, build_final_dataset.py 기준) ─
CURATE_PURE_RATIO  = 0.4
CURATE_INF_RATIO   = 0.7
AUTO_PURE_RATIO    = 0.6
REVIEW_PURE_RATIO  = 0.4
REVIEW_INF_RATIO   = 0.7
REVIEW_PURE_RANK   = 2000
REVIEW_INF_RANK    = 1000
REVIEW_INF_MIN     = 0.4
REVIEW_PURE_MIN    = 0.1


def _is_curated(r: dict) -> bool:
    if r["slang_tier"] == "pure_slang":
        return r["slang_ratio"] >= CURATE_PURE_RATIO
    if r["slang_tier"] == "informal_slang":
        return r["slang_ratio"] >= CURATE_INF_RATIO
    return False


def _is_auto(r: dict) -> bool:
    return r["slang_tier"] == "pure_slang" and r["slang_ratio"] >= AUTO_PURE_RATIO


def _is_review(r: dict) -> bool:
    if _is_auto(r):
        return False
    tier  = r["slang_tier"]
    ratio = r["slang_ratio"]
    rank  = r["rank"]
    if tier == "pure_slang":
        if ratio >= REVIEW_PURE_RATIO:
            return True
        if ratio >= REVIEW_PURE_MIN and rank <= REVIEW_PURE_RANK:
            return True
    if tier == "informal_slang":
        if ratio >= REVIEW_INF_RATIO:
            return True
        if ratio >= REVIEW_INF_MIN and rank <= REVIEW_INF_RANK:
            return True
    return False


# ── Main ─────────────────────────────────────────────────────────────────────
def enrich(row: dict) -> dict:
    word = row["word"]

    # 1. Patch definitions
    if word in DEFINITION_PATCHES:
        row.update(DEFINITION_PATCHES[word])

    # 2. Difficulty tier
    row["difficulty_tier"] = difficulty_tier(row["rank"])

    # 3. Validate examples
    original_count = len(row.get("reddit_examples") or [])
    row["reddit_examples"] = validate_examples(word, row.get("reddit_examples") or [])
    after_count = len(row["reddit_examples"])
    if after_count < original_count:
        row.setdefault("_removed_examples", original_count - after_count)

    return row


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = []
    with FINAL_PATH.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    print(f"[INFO] Loaded {len(rows)} words from {FINAL_PATH}")

    patch_count    = 0
    removed_ex     = 0
    tier_counts: dict[str, int] = {}

    enriched = []
    for r in rows:
        before_def = r.get("definition_en", "")
        r = enrich(r)
        if r.get("definition_en") != before_def:
            patch_count += 1
        removed_ex += r.pop("_removed_examples", 0)
        tier_counts[r["difficulty_tier"]] = tier_counts.get(r["difficulty_tier"], 0) + 1
        enriched.append(r)

    write_jsonl(enriched, FINAL_PATH)
    print(f"\n[DONE] final_dataset.jsonl updated ({len(enriched)} words)")
    print(f"  정의 패치: {patch_count}개")
    print(f"  제거된 예문: {removed_ex}개")
    print(f"  difficulty_tier: {tier_counts}")

    # Regenerate subsets
    curated = [r for r in enriched if _is_curated(r)]
    for r in curated:
        r.setdefault("review_status", None)

    auto_rows   = [dict(r, review_status="auto_approved") for r in enriched if _is_auto(r)]
    review_rows = [dict(r, review_status="needs_review")  for r in enriched if _is_review(r)]

    write_jsonl(curated,     CURATED_PATH)
    write_jsonl(auto_rows,   AUTO_PATH)
    write_jsonl(review_rows, REVIEW_PATH)

    print(f"\n  curated_dataset  → {CURATED_PATH}  ({len(curated)}개)")
    print(f"  auto_approved    → {AUTO_PATH}  ({len(auto_rows)}개)")
    print(f"  needs_review     → {REVIEW_PATH}  ({len(review_rows)}개)")

    # Spot-check patched words
    lookup = {r["word"]: r for r in enriched}
    print("\n=== 패치된 단어 확인 ===")
    for w in DEFINITION_PATCHES:
        e = lookup.get(w)
        if e:
            print(f"  {w:<8} tier={e['difficulty_tier']:<12} "
                  f"slang_tier={e['slang_tier']:<16} "
                  f"def=\"{e['definition_en'][:60]}\"")
        else:
            print(f"  {w:<8} (dataset에 없음)")


if __name__ == "__main__":
    main()
