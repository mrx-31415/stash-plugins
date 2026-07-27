#!/usr/bin/env python3
"""Run controlled Cover Story headshot prompt experiments."""

import argparse
import hashlib
import itertools
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from comfy import prepare, run


AGE_CUES = [
    ("literal-age", "exactly 29 years old, with naturally youthful adult features and smooth healthy late-twenties skin"),
    ("youthful", "a youthful-looking 29-year-old rising star with fresh adult features and only subtle natural expression lines"),
    ("late-twenties", "unmistakably in her late twenties rather than mature, with bright eyes and softly defined youthful features"),
]
GLAMOUR_CUES = [
    ("cosmetics", "photographed as the face of a luxury cosmetics campaign, with magnetic star presence and sculpted beauty lighting"),
    ("red-carpet", "styled for an A-list red-carpet publicity portrait, polished, radiant, and unmistakably glamorous"),
    ("fashion", "photographed for a high-fashion beauty editorial, striking, sophisticated, and intensely photogenic"),
    ("hollywood", "photographed as a glamorous Hollywood leading lady, captivating and larger-than-life without looking artificial"),
]
MAKEUP_CUES = [
    ("soft-glam", "polished soft-glam makeup, luminous complexion, defined lashes and brows, subtle contour, and glossy natural lips"),
    ("full-glam", "camera-ready glamour makeup, softly smoky eyes, long defined lashes, sculpted cheekbones, radiant skin, and rich satin lips"),
]
CALIBRATION = [
    (
        f"{age[0]}-{glamour[0]}-full-glam",
        ". ".join(part[0].upper() + part[1:] for part in (age[1], glamour[1], MAKEUP_CUES[1][1])) + ".",
    )
    for age, glamour in itertools.product(AGE_CUES, GLAMOUR_CUES)
]

PERFORMER_PROFILES = [
    {"slug": "orla-hart", "name": "Orla Hart", "age": 25, "identity": "Irish actress of Irish heritage", "appearance": "fair freckled skin and green eyes", "hair": "long copper waves", "feature": "a tiny gold nose stud", "build": "an athletic, curvy build", "wardrobe": "forest-green scoop-neck knit top", "pose": "Square to camera with relaxed shoulders and a confident closed-lip smile"},
    {"slug": "mara-bellini", "name": "Mara Bellini", "age": 31, "identity": "Italian actress of southern Italian heritage", "appearance": "warm olive skin and hazel eyes", "hair": "deep auburn curls", "feature": "a beauty mark above her lip", "build": "a curvy build", "wardrobe": "plum boat-neck top", "pose": "Three-quarter left with a knowing half-smile"},
    {"slug": "elin-nystrom", "name": "Elin Nyström", "age": 27, "identity": "Swedish actress of Scandinavian heritage", "appearance": "fair skin and blue eyes", "hair": "strawberry-blonde layers", "feature": "delicate stacked ear piercings", "build": "a petite build", "wardrobe": "navy ribbed top", "pose": "Chin slightly lowered with a bright open smile"},
    {"slug": "zuri-adebayo", "name": "Zuri Adebayo", "age": 29, "identity": "British actress of Nigerian Yoruba heritage", "appearance": "deep brown skin and amber eyes", "hair": "burgundy-tinted natural coils", "feature": "a small eyebrow piercing", "build": "a full-figured build", "wardrobe": "emerald square-neck knit top", "pose": "Three-quarter right with a calm, intense direct gaze"},
    {"slug": "clara-beaulieu", "name": "Clara Beaulieu", "age": 34, "identity": "Canadian actress of French-Canadian heritage", "appearance": "fair skin and blue-gray eyes", "hair": "long espresso-brown waves", "feature": "a botanical collarbone tattoo", "build": "a tall, statuesque build", "wardrobe": "burgundy wide-neck top", "pose": "Shoulders angled with a thoughtful gaze just past the camera"},
    {"slug": "maya-valenca", "name": "Maya Valença", "age": 24, "identity": "Brazilian actress of mixed Afro-European Brazilian heritage", "appearance": "light-brown skin and hazel eyes", "hair": "voluminous chestnut curls", "feature": "a pronounced cupid's bow", "build": "a curvy build", "wardrobe": "teal scoop-neck top", "pose": "Gentle head tilt with a warm, confident smile"},
    {"slug": "sofia-marin", "name": "Sofia Marín", "age": 36, "identity": "Spanish actress of Andalusian heritage", "appearance": "olive skin and green eyes", "hair": "a glossy dark-brown bob", "feature": "a delicate silver nose ring", "build": "an athletic build", "wardrobe": "rust-colored ribbed top", "pose": "Square to camera with a composed expression and one eyebrow subtly raised"},
    {"slug": "nia-arden", "name": "Nia Arden", "age": 28, "identity": "American actress of African-American heritage", "appearance": "medium-brown skin and dark eyes", "hair": "long layered brunette hair", "feature": "a floral upper-arm tattoo", "build": "a soft, curvy build", "wardrobe": "cobalt knit top", "pose": "Three-quarter pose with a relaxed, genuine smile"},
    {"slug": "ama-mensah", "name": "Ama Mensah", "age": 26, "identity": "Ghanaian actress of Akan heritage", "appearance": "deep dark-brown skin and dark eyes", "hair": "long black natural coils", "feature": "a tiny gold nose stud", "build": "a full-figured build", "wardrobe": "saffron scoop-neck top", "pose": "Direct gaze with a serene, self-assured expression"},
    {"slug": "naomi-kamau", "name": "Naomi Kamau", "age": 32, "identity": "British actress of Kenyan heritage", "appearance": "deep brown skin and warm brown eyes", "hair": "long neat box braids", "feature": "multiple small gold ear piercings", "build": "a tall, athletic build", "wardrobe": "wine-red top", "pose": "Angled shoulders with a broad, friendly smile"},
    {"slug": "hana-mori", "name": "Hana Mori", "age": 30, "identity": "Japanese actress of Japanese heritage", "appearance": "light-medium skin and dark almond-shaped eyes", "hair": "a sleek black bob", "feature": "a polished ear cuff", "build": "a petite build", "wardrobe": "jade-green knit top", "pose": "Three-quarter pose with a poised neutral expression"},
    {"slug": "priya-sethi", "name": "Priya Sethi", "age": 27, "identity": "British actress of Indian Punjabi heritage", "appearance": "warm-brown skin and large dark eyes", "hair": "long glossy black waves", "feature": "a delicate jeweled nose stud", "build": "a curvy build", "wardrobe": "plum-colored top", "pose": "Slight lean toward the camera with a magnetic direct gaze"},
    {"slug": "iga-kowalska", "name": "Iga Kowalska", "age": 23, "identity": "Polish actress of Polish heritage", "appearance": "pale skin and gray eyes", "hair": "a sharp jet-black pixie cut", "feature": "a subtle eyebrow piercing", "build": "a slender build", "wardrobe": "charcoal wide-neck top", "pose": "Chin slightly raised with a cool, self-assured expression"},
    {"slug": "leila-haddad", "name": "Leila Haddad", "age": 33, "identity": "Lebanese actress of Lebanese Arab heritage", "appearance": "warm beige skin and brown eyes", "hair": "long honey-blonde waves", "feature": "a small shoulder tattoo", "build": "a curvy build", "wardrobe": "deep-teal knit top", "pose": "Gentle head tilt with a soft, luminous smile"},
    {"slug": "greta-lane", "name": "Greta Lane", "age": 26, "identity": "Australian actress of Anglo-Australian heritage", "appearance": "fair skin and bright blue eyes", "hair": "tousled golden-blonde hair", "feature": "soft freckles across her nose", "build": "an athletic build", "wardrobe": "aubergine scoop-neck top", "pose": "Square to camera with an energetic natural grin"},
    {"slug": "elise-jonsdottir", "name": "Elise Jónsdóttir", "age": 29, "identity": "Icelandic actress of Icelandic heritage", "appearance": "pale skin and dark-brown eyes", "hair": "a platinum-blonde blunt lob", "feature": "multiple silver ear piercings", "build": "a slender build", "wardrobe": "black square-neck top", "pose": "Three-quarter pose with a composed serious expression"},
    {"slug": "valentina-rossi", "name": "Valentina Rossi", "age": 35, "identity": "Argentine actress of Italian-Argentine heritage", "appearance": "lightly tanned skin and green eyes", "hair": "thick dark-blonde waves", "feature": "a distinctive dimple", "build": "an athletic, curvy build", "wardrobe": "cobalt ribbed top", "pose": "Angled shoulders with a restrained mid-laugh smile"},
    {"slug": "vera-kovalenko", "name": "Vera Kovalenko", "age": 43, "identity": "Ukrainian actress of Ukrainian heritage", "appearance": "fair skin and blue-gray eyes", "hair": "polished silver-blonde waves", "feature": "a graceful beauty mark", "build": "an elegant, curvy build", "wardrobe": "navy knit top", "pose": "Square to camera with a graceful closed-lip smile"},
    {"slug": "jules-navarro", "name": "Jules Navarro", "age": 25, "identity": "American actress of Mexican-American heritage", "appearance": "medium skin and brown eyes", "hair": "a side-swept brunette undercut", "feature": "an artistic neck tattoo and small nose ring", "build": "a compact, athletic build", "wardrobe": "oxblood top", "pose": "Three-quarter pose with an assertive direct gaze"},
    {"slug": "leyla-demir", "name": "Leyla Demir", "age": 30, "identity": "Turkish actress of Turkish heritage", "appearance": "olive skin and hazel eyes", "hair": "brunette waves with a bold blonde face-framing streak", "feature": "a thin gold nose ring", "build": "a slim, curvy build", "wardrobe": "emerald knit top", "pose": "Slight head tilt with a playful half-smile"},
    {"slug": "simone-gray", "name": "Simone Gray", "age": 48, "identity": "Jamaican actress of Afro-Jamaican heritage", "appearance": "deep brown skin and warm eyes", "hair": "glamorous natural-gray curls", "feature": "elegant statement earrings", "build": "a full-figured build", "wardrobe": "ruby-red top", "pose": "Direct gaze with a warm, assured smile"},
    {"slug": "dahlia-cruz", "name": "Dahlia Cruz", "age": 39, "identity": "Colombian actress of mixed Colombian heritage", "appearance": "golden-brown skin and amber eyes", "hair": "long dark-brown curls", "feature": "a colorful upper-arm tattoo", "build": "a curvy build", "wardrobe": "turquoise top", "pose": "Looking slightly past the camera with a self-possessed expression"},
    {"slug": "nandi-dlamini", "name": "Nandi Dlamini", "age": 37, "identity": "South African actress of Zulu heritage", "appearance": "deep brown skin and dark eyes", "hair": "blue-black shoulder-length curls", "feature": "a geometric collarbone tattoo", "build": "a statuesque build", "wardrobe": "copper-colored top", "pose": "Chin slightly lowered with a focused, intense gaze"},
    {"slug": "rowan-pierce", "name": "Rowan Pierce", "age": 28, "identity": "Scottish actress of Scottish heritage", "appearance": "fair freckled skin and green eyes", "hair": "a textured copper pixie cut", "feature": "three small ear studs", "build": "a petite, athletic build", "wardrobe": "violet knit top", "pose": "Shoulders turned slightly with a mischievous closed-lip smile"},
]


def expansion_profile(slug, name, age, identity, appearance, hair, feature, build, ethnicity, hair_group, build_group):
    return {
        "slug": slug, "name": name, "age": age, "identity": identity,
        "appearance": appearance, "hair": hair, "feature": feature, "build": build,
        "ethnicity": ethnicity, "hair_group": hair_group, "build_group": build_group,
    }


# Sized from the performers-v3 51% keep rate: 200 candidates should add about
# 101 approved identities to the existing 49. The mix fills live-library gaps:
# older ages, Caucasian performers with black hair, and fewer curvy-only builds.
EXPANSION_PROFILES = [
    expansion_profile("evelyn-cross", "Evelyn Cross", 27, "American actress of Irish-American heritage", "fair skin and clear blue eyes", "long glossy jet-black waves", "a tiny silver nose stud", "a slender build", "Caucasian", "Black", "Slender"),
    expansion_profile("tessa-ward", "Tessa Ward", 29, "American actress of English-American heritage", "fair neutral skin and hazel eyes", "long chestnut-brown layers", "soft freckles across her nose", "an athletic build", "Caucasian", "Brunette", "Average"),
    expansion_profile("claire-novak", "Claire Novak", 30, "American actress of Czech heritage", "fair skin and green eyes", "a glossy jet-black chin-length bob", "a delicate ear cuff", "a petite build", "Caucasian", "Black", "Slender"),
    expansion_profile("rachel-stein", "Rachel Stein", 31, "American actress of Ashkenazi Jewish heritage", "fair olive-toned skin and brown eyes", "thick dark-auburn curls", "a beauty mark on her right cheek", "a curvy build", "Caucasian", "Various", "Curvy"),
    expansion_profile("megan-holt", "Megan Holt", 32, "American actress of German-American heritage", "fair skin and blue eyes", "shoulder-length sandy-blonde waves", "small gold hoop earrings", "an athletic build", "Caucasian", "Blonde", "Average"),
    expansion_profile("brooke-mercer", "Brooke Mercer", 33, "American actress of English-American heritage", "fair peach-toned skin and green eyes", "a honey-blonde layered lob", "a subtle eyebrow piercing", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("natalia-orlov", "Natalia Orlov", 34, "American actress of Russian heritage", "pale skin and gray eyes", "long espresso-brown straight hair", "a small geometric wrist tattoo", "a slender build", "Caucasian", "Brunette", "Slender"),
    expansion_profile("camille-laurent", "Camille Laurent", 32, "Canadian actress of French-Canadian heritage", "fair skin and blue-gray eyes", "soft medium-brown waves", "a pronounced cupid's bow", "an average build", "Caucasian", "Brunette", "Average"),
    expansion_profile("petra-svoboda", "Petra Svoboda", 33, "Czech actress of Czech heritage", "fair skin and hazel eyes", "a rich brunette blunt bob", "stacked silver ear piercings", "an athletic build", "Caucasian", "Brunette", "Average"),
    expansion_profile("ana-torres", "Ana Torres", 34, "American actress of Mexican-American heritage", "light-medium golden skin and brown eyes", "long dark-brown curls", "a fine-line shoulder tattoo", "an average build", "Latin", "Brunette", "Average"),
    expansion_profile("lauren-price", "Lauren Price", 35, "American actress of English-American heritage", "fair skin and blue eyes", "long champagne-blonde waves", "a delicate gold nose stud", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("heather-cole", "Heather Cole", 36, "American actress of Scottish-American heritage", "fair freckled skin and green eyes", "wheat-blonde feathered layers", "three small ear studs", "an athletic build", "Caucasian", "Blonde", "Average"),
    expansion_profile("monica-reed", "Monica Reed", 37, "American actress of German-American heritage", "fair warm skin and brown eyes", "voluminous chocolate-brown waves", "a small floral collarbone tattoo", "a curvy build", "Caucasian", "Brunette", "Curvy"),
    expansion_profile("dana-walsh", "Dana Walsh", 38, "American actress of Irish-American heritage", "fair skin and blue-gray eyes", "shoulder-length glossy black waves", "a tiny gold eyebrow piercing", "an average build", "Caucasian", "Black", "Average"),
    expansion_profile("erin-blake", "Erin Blake", 39, "American actress of Welsh-American heritage", "fair skin and hazel eyes", "a textured dark-brown pixie cut", "a graceful beauty mark", "a slender build", "Caucasian", "Brunette", "Slender"),
    expansion_profile("kristina-volkov", "Kristina Volkov", 35, "American actress of Ukrainian heritage", "fair cool-toned skin and gray eyes", "polished champagne-blonde waves", "multiple small silver ear piercings", "an athletic build", "Caucasian", "Blonde", "Average"),
    expansion_profile("sabrina-keller", "Sabrina Keller", 36, "American actress of Swiss-German heritage", "fair skin and green eyes", "a sleek espresso-brown bob", "a slim gold nose ring", "a slender build", "Caucasian", "Brunette", "Slender"),
    expansion_profile("keisha-morgan", "Keisha Morgan", 37, "American actress of African-American heritage", "medium-deep brown skin and warm brown eyes", "long blue-black natural curls", "a colorful upper-arm tattoo", "a curvy build", "Black", "Black", "Curvy"),
    expansion_profile("rebecca-snow", "Rebecca Snow", 38, "American actress of Polish heritage", "pale skin and dark-brown eyes", "long straight jet-black hair", "a small silver nose stud", "a petite build", "Caucasian", "Black", "Slender"),
    expansion_profile("katerina-dvorak", "Katerina Dvorak", 39, "Czech actress of Czech heritage", "fair skin and green eyes", "thick chestnut-brown waves", "a tiny star tattoo behind one ear", "an athletic build", "Caucasian", "Brunette", "Average"),
    expansion_profile("alina-petrova", "Alina Petrova", 37, "Russian actress of Russian heritage", "fair skin and bright blue eyes", "a cool-blonde shoulder-length lob", "a distinctive dimple", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("mei-chen", "Mei Chen", 38, "American actress of Chinese heritage", "light-medium skin and dark-brown almond-shaped eyes", "long sleek black hair", "a polished silver ear cuff", "a slender build", "Asian", "Black", "Slender"),
    expansion_profile("lucie-moreau", "Lucie Moreau", 36, "French actress of French heritage", "fair skin and blue eyes", "a soft golden-blonde bob", "a beauty mark above her lip", "a petite build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("ingrid-bauer", "Ingrid Bauer", 39, "German actress of German heritage", "fair skin and gray eyes", "dark brunette hair with a bold copper streak", "minimalist stacked ear piercings", "a slender build", "Caucasian", "Various", "Slender"),
    expansion_profile("michelle-grant", "Michelle Grant", 40, "American actress of English-American heritage", "fair neutral skin and brown eyes", "long deep-brown waves", "a delicate collarbone tattoo", "an athletic build", "Caucasian", "Brunette", "Average"),
    expansion_profile("jennifer-hale", "Jennifer Hale", 41, "American actress of Irish-American heritage", "fair freckled skin and green eyes", "warm golden-blonde layers", "a small gold nose ring", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("laura-benson", "Laura Benson", 42, "American actress of German-American heritage", "fair skin and hazel eyes", "voluminous dark-brown curls", "a distinctive cheek dimple", "a curvy build", "Caucasian", "Brunette", "Curvy"),
    expansion_profile("stephanie-moss", "Stephanie Moss", 43, "American actress of Dutch-American heritage", "fair skin and blue eyes", "a polished beige-blonde lob", "small pearl ear studs", "an average build", "Caucasian", "Blonde", "Average"),
    expansion_profile("nicole-avery", "Nicole Avery", 44, "American actress of French-American heritage", "fair olive-toned skin and green eyes", "long espresso-brown layers", "a fine-line botanical shoulder tattoo", "a petite build", "Caucasian", "Brunette", "Slender"),
    expansion_profile("amanda-pierce", "Amanda Pierce", 45, "American actress of English-American heritage", "fair warm skin and blue-gray eyes", "thick honey-blonde waves", "a tiny gold nose stud", "a curvy build", "Caucasian", "Blonde", "Curvy"),
    expansion_profile("kimberly-shaw", "Kimberly Shaw", 46, "American actress of Scottish-American heritage", "fair skin and green eyes", "a glossy jet-black shoulder-length cut", "multiple silver ear piercings", "an athletic build", "Caucasian", "Black", "Average"),
    expansion_profile("christine-dale", "Christine Dale", 47, "American actress of Polish-American heritage", "pale skin and gray eyes", "soft honey-blonde waves", "a subtle eyebrow piercing", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("angela-stone", "Angela Stone", 48, "American actress of Italian-American heritage", "fair olive skin and brown eyes", "long rich brunette curls", "a beauty mark above her lip", "a curvy build", "Caucasian", "Brunette", "Curvy"),
    expansion_profile("melissa-frost", "Melissa Frost", 49, "American actress of Norwegian-American heritage", "fair skin and blue eyes", "a pale-blonde layered bob", "small geometric ear studs", "an average build", "Caucasian", "Blonde", "Average"),
    expansion_profile("gabriela-reyes", "Gabriela Reyes", 40, "Colombian actress of mixed Colombian heritage", "golden-brown skin and amber eyes", "long dark-brown curls", "a colorful upper-arm tattoo", "a slender build", "Latin", "Brunette", "Slender"),
    expansion_profile("katherine-west", "Katherine West", 41, "American actress of German-American heritage", "fair skin and blue-gray eyes", "long champagne-blonde waves", "a graceful beauty mark", "an athletic build", "Caucasian", "Blonde", "Average"),
    expansion_profile("andrea-quinn", "Andrea Quinn", 42, "American actress of Irish-American heritage", "fair freckled skin and green eyes", "a sharp jet-black pixie cut", "a tiny silver nose ring", "a petite build", "Caucasian", "Black", "Slender"),
    expansion_profile("danielle-page", "Danielle Page", 43, "American actress of French-American heritage", "fair skin and hazel eyes", "soft chocolate-brown waves", "stacked gold ear piercings", "an average build", "Caucasian", "Brunette", "Average"),
    expansion_profile("bethany-clarke", "Bethany Clarke", 44, "American actress of English-American heritage", "fair peach-toned skin and blue eyes", "a golden-blonde shoulder-length lob", "a small floral wrist tattoo", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("oksana-melnyk", "Oksana Melnyk", 45, "Ukrainian actress of Ukrainian heritage", "fair skin and clear blue eyes", "polished wheat-blonde waves", "a delicate silver ear cuff", "an athletic build", "Caucasian", "Blonde", "Average"),
    expansion_profile("samira-okafor", "Samira Okafor", 46, "British actress of mixed Nigerian and English heritage", "medium-brown skin and hazel eyes", "long black curls", "a thin gold nose ring", "a curvy build", "Mixed", "Black", "Curvy"),
    expansion_profile("marta-zielinska", "Marta Zielinska", 47, "Polish actress of Polish heritage", "fair skin and gray eyes", "a warm beige-blonde blunt bob", "a small shoulder tattoo", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("lenka-kralova", "Lenka Kralova", 49, "Czech actress of Czech heritage", "fair skin and green eyes", "dark-brown waves with subtle caramel streaks", "a pronounced cupid's bow", "an average build", "Caucasian", "Various", "Average"),
    expansion_profile("susan-archer", "Susan Archer", 50, "American actress of English-American heritage", "fair skin and blue eyes", "soft golden-blonde waves", "elegant small hoop earrings", "a curvy build", "Caucasian", "Blonde", "Curvy"),
    expansion_profile("diane-monroe", "Diane Monroe", 52, "American actress of Irish-American heritage", "fair freckled skin and hazel eyes", "rich brunette shoulder-length curls", "a delicate gold nose stud", "a curvy build", "Caucasian", "Brunette", "Curvy"),
    expansion_profile("karen-whitmore", "Karen Whitmore", 54, "American actress of German-American heritage", "fair skin and blue-gray eyes", "a polished warm-blonde lob", "multiple small ear piercings", "a slender build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("darya-azadi", "Darya Azadi", 56, "American actress of Iranian heritage", "warm olive skin and dark-brown eyes", "long glossy black waves", "a fine-line collarbone tattoo", "a curvy build", "Middle Eastern", "Black", "Curvy"),
    expansion_profile("margaret-ellis", "Margaret Ellis", 58, "British actress of Welsh heritage", "fair skin and green eyes", "deep chestnut shoulder-length waves", "a distinctive cheek dimple", "an athletic build", "Caucasian", "Various", "Average"),
    expansion_profile("linda-carver", "Linda Carver", 51, "American actress of Dutch-American heritage", "fair skin and blue eyes", "a warm-blonde layered bob", "small gold ear studs", "a petite build", "Caucasian", "Blonde", "Slender"),
    expansion_profile("patricia-rowan", "Patricia Rowan", 55, "Australian actress of English-Australian heritage", "fair warm skin and brown eyes", "soft chestnut-brown waves", "a graceful beauty mark", "a slender build", "Caucasian", "Brunette", "Slender"),
]


def age_band(age):
    return "25-29" if age < 30 else "30-34" if age < 35 else "35-39" if age < 40 else "40-49" if age < 50 else "50+"


assert len(EXPANSION_PROFILES) == 50
assert Counter(profile["ethnicity"] for profile in EXPANSION_PROFILES) == {
    "Caucasian": 44, "Latin": 2, "Black": 1, "Asian": 1, "Mixed": 1, "Middle Eastern": 1,
}
assert Counter(profile["hair_group"] for profile in EXPANSION_PROFILES) == {
    "Blonde": 19, "Brunette": 17, "Black": 10, "Various": 4,
}
assert Counter(profile["build_group"] for profile in EXPANSION_PROFILES) == {
    "Slender": 22, "Average": 18, "Curvy": 10,
}
assert Counter(age_band(profile["age"]) for profile in EXPANSION_PROFILES) == {
    "25-29": 2, "30-34": 8, "35-39": 14, "40-49": 19, "50+": 7,
}

QUALITY = [
    {"name": "q4-6-euler", "model": "Krea-2-Turbo-Q4_K_M.gguf", "steps": 6, "cfg": 1, "sampler": "euler", "clean": False},
    {"name": "q4-8-euler", "model": "Krea-2-Turbo-Q4_K_M.gguf", "steps": 8, "cfg": 1, "sampler": "euler", "clean": False},
    {"name": "q5-8-euler", "model": "Krea-2-Turbo-Q5_K_S.gguf", "steps": 8, "cfg": 1, "sampler": "euler", "clean": False},
    {"name": "q5-8-er-sde", "model": "Krea-2-Turbo-Q5_K_S.gguf", "steps": 8, "cfg": 1, "sampler": "er_sde", "clean": False},
    {"name": "q5-8-clean-prompt", "model": "Krea-2-Turbo-Q5_K_S.gguf", "steps": 8, "cfg": 1, "sampler": "euler", "clean": True},
    {"name": "base-q4-52-euler", "model": "Krea-2-Base-Q4_K_M.gguf", "steps": 52, "cfg": 3.5, "sampler": "euler", "clean": True},
    {"name": "base-q5-52-euler", "model": "Krea-2-Base-Q5_K_M.gguf", "steps": 52, "cfg": 3.5, "sampler": "euler", "clean": True},
]

assert len(CALIBRATION) == 12 and len(PERFORMER_PROFILES) == 24


def calibration_prompt(cue):
    return (
        "Relaxed waist-up cinematic beauty portrait of a fictional adult actress with fair "
        "freckled skin, large green eyes, defined cheekbones, full natural lips, long copper-red "
        f"hair in loose waves, and a shapely curvy feminine figure. {cue} She wears a fashionable "
        "fitted black knit top with a tasteful wide neckline, no blazer, button-down shirt, uniform, "
        "or businesswear. Clean studio beauty lighting, warm neutral background, eye-level 85mm "
        "portrait lens, clean beauty-retouch finish, fine natural pores, smooth tonal gradients, "
        "fully clothed, workplace-safe, "
        "confident non-provocative pose, no text, logo, or watermark, and no resemblance to any real "
        "person or public figure."
    )


CONTROL_SUBJECT = (
    "Studio portrait of a strikingly beautiful fictional 29-year-old actress with fair freckled "
    "skin, green eyes, defined cheekbones, full natural lips, and long copper-red hair."
)
CONTROL_COMPOSITION = CONTROL_SUBJECT + (
    " Waist-up composition, wearing a fitted black knit top with a wide neckline."
)
CONTROL_GLAMOUR = CONTROL_COMPOSITION + (
    " Camera-ready glamour makeup with softly smoky eyes, defined lashes, sculpted cheekbones, "
    "radiant skin, and satin lips."
)
CONTROL_PHOTOGRAPHY = CONTROL_GLAMOUR + (
    " Clean studio beauty lighting, warm neutral background, eye-level 85mm portrait lens, "
    "fine natural pores, and smooth tonal gradients."
)
PROMPT_CONTROL = [
    ("minimal", "Studio portrait of a strikingly beautiful fictional 29-year-old actress with long copper-red hair."),
    ("subject-detail", CONTROL_SUBJECT),
    ("plus-composition", CONTROL_COMPOSITION),
    ("plus-glamour", CONTROL_GLAMOUR),
    ("plus-photography", CONTROL_PHOTOGRAPHY),
    ("full-positive", CONTROL_PHOTOGRAPHY + " Confident relaxed pose, fully clothed."),
    ("natural-casting", "Natural professional casting headshot of a beautiful fictional 29-year-old red-haired actress, minimal makeup, warm studio background."),
    ("cosmetics", "Luxury cosmetics campaign portrait of a strikingly beautiful fictional 29-year-old red-haired actress, luminous glamour makeup, clean beauty lighting."),
    ("fashion", "High-fashion beauty editorial portrait of a strikingly beautiful fictional 29-year-old red-haired actress, sophisticated styling and sculpted studio light."),
    ("red-carpet", "A-list red-carpet publicity portrait of a strikingly beautiful fictional 29-year-old red-haired actress, polished radiant glamour."),
    ("hollywood", "Classic Hollywood publicity portrait of a captivating fictional 29-year-old red-haired leading actress, glamorous and larger-than-life."),
    ("legacy-maximal", calibration_prompt(CALIBRATION[6][1])),
]
assert len(PROMPT_CONTROL) == 12
STABILITY = [
    (f"seed-{seed_number:02d}-{name}", text, seed_number)
    for seed_number in range(1, 5)
    for name, text in (
        ("minimal", PROMPT_CONTROL[0][1]),
        ("plus-glamour", CONTROL_GLAMOUR),
        ("plus-photography", CONTROL_PHOTOGRAPHY),
    )
]
assert len(STABILITY) == 12
IDENTITY_CONTROL_A = (
    "Studio portrait of Kristina Volkov, a strikingly beautiful, conventionally attractive fictional "
    "American actress of Ukrainian heritage in her mid thirties, with a vibrant, well-rested contemporary "
    "appearance appropriate to her age band. She has fair cool-toned skin and gray eyes, a pear-shaped face "
    "with a defined lower contour, a gently hooked nose, smooth high-set cheeks, a softly defined chin, long "
    "black curls with a center part, and a delicate ear cuff. Shoulders gently angled, her face fully toward "
    "the camera, with a relaxed open smile. Waist-up composition; she has a compact athletic build with a "
    "medium bust and wears a casual dark denim shirt with the collar open, against a softly blurred modern "
    "dressing-room interior with a cool-gray wall, indistinct framed decor, and a warm practical lamp, with "
    "broad low-contrast diffused beauty light and gentle frontal facial fill. Polished camera-ready makeup "
    "suited to her complexion, with defined eyes, luminous natural-looking skin, and satin lips."
)
IDENTITY_CONTROL_B = (
    "Studio portrait of Kristina Ashford, a strikingly beautiful, conventionally attractive fictional "
    "American actress of Ukrainian heritage in her late thirties, with a vibrant, well-rested contemporary "
    "appearance appropriate to her age band. She has fair cool-toned skin and gray eyes, an inverted-triangle "
    "face with softened angles, a softly flared nose, prominent cheekbones, a strong rounded chin, "
    "shoulder-length dark-cherry waves, and a distinctive cheek dimple. Relaxed three-quarter pose with a calm "
    "direct gaze and a slight smile. Waist-up composition; she has a strong, athletic build with a full bust "
    "and wears a casual short-sleeve jersey top with a moderate scoop neckline in a muted earth tone, against "
    "a softly blurred sunlit loft interior with pale walls, a distant window, and an out-of-focus green plant. "
    "Polished camera-ready makeup suited to her complexion, with defined eyes, luminous natural-looking skin, "
    "and satin lips."
)
IDENTITY_DIVERSITY = [
    ("a-control", IDENTITY_CONTROL_A, 153_028_830_359),
    (
        "a-no-beauty",
        IDENTITY_CONTROL_A.replace(
            "a strikingly beautiful, conventionally attractive fictional", "a fictional"
        ),
        153_028_830_359,
    ),
    (
        "a-independent",
        "Studio portrait of Nadia Moroz, a charismatic, distinctive-looking fictional Ukrainian-American "
        "actress in her mid thirties, with an energetic contemporary presence. She has fair neutral skin with "
        "scattered freckles, deep-set hazel eyes, thick straight brows, a softly rectangular face, a prominent "
        "aquiline nose, low defined cheekbones, a wide angular jaw, a fuller lower lip, long black curls with a "
        "center part, and a delicate ear cuff. Shoulders gently angled, her face fully toward the camera, with "
        "a relaxed open smile. Waist-up composition; she has a compact athletic build with a medium bust and "
        "wears a casual dark denim shirt with the collar open, against a softly blurred modern dressing-room "
        "interior with a cool-gray wall, indistinct framed decor, and a warm practical lamp. Understated "
        "editorial makeup with natural skin texture, softly defined eyes, and muted lips.",
        153_028_830_359,
    ),
    ("b-control", IDENTITY_CONTROL_B, 154_028_830_359),
    (
        "b-no-beauty",
        IDENTITY_CONTROL_B.replace(
            "a strikingly beautiful, conventionally attractive fictional", "a fictional"
        ),
        154_028_830_359,
    ),
    (
        "b-independent",
        "Studio portrait of Olesya Varen, a classically elegant, highly photogenic fictional "
        "Ukrainian-American actress in her late thirties, with a poised modern presence. She has light olive "
        "skin, wide-set green eyes, fine arched brows, a compact heart-shaped face, a short upturned nose, high "
        "wide cheekbones, a narrow tapered jaw, a small left-cheek dimple, and shoulder-length dark-cherry "
        "waves. Relaxed three-quarter pose with a calm direct gaze and a slight smile. Waist-up composition; "
        "she has a strong athletic build with a full bust and wears a casual short-sleeve jersey top with a "
        "moderate scoop neckline in a muted earth tone, against a softly blurred sunlit loft interior with pale "
        "walls, a distant window, and an out-of-focus green plant. Refined natural makeup with softly luminous "
        "skin, subtle eyeliner, and a rose-neutral lip.",
        154_028_830_359,
    ),
]
assert len(IDENTITY_DIVERSITY) == 6
PORTRAIT_STYLES = [
    {
        "name": "cool-gray-ribbed",
        "kind": "environment",
        "pose": "Subtle three-quarter angle with relaxed shoulders, her face fully toward the camera, and a warm confident smile",
        "wardrobe": "casual crew-neck ribbed knit top in a rich jewel tone",
        "background": "softly blurred modern dressing-room interior with a cool-gray wall, indistinct framed decor, and a warm practical lamp, with broad low-contrast diffused beauty light and gentle frontal facial fill",
    },
    {
        "name": "warm-window-jersey",
        "kind": "environment",
        "pose": "Subtle three-quarter angle with relaxed shoulders, her face fully toward the camera, and a soft natural smile",
        "wardrobe": "casual short-sleeve jersey top with a moderate scoop neckline in a muted earth tone",
        "background": "warm beige studio interior with a softly blurred linen curtain and window edge behind her, with clean soft light",
    },
    {
        "name": "pale-sage-boatneck",
        "kind": "environment",
        "pose": "Shoulders slightly angled, her face fully toward the camera, with a poised soft half-smile",
        "wardrobe": "casual soft boat-neck knit top in a deep cool color",
        "background": "softly blurred contemporary lounge with a pale-sage wall, indistinct shelving and plant shapes, with broad low-contrast diffused beauty light and gentle frontal facial fill",
    },
    {
        "name": "blue-window-henley",
        "kind": "environment",
        "pose": "Gentle head tilt with relaxed shoulders, her face fully toward the camera, and a warm confident expression",
        "wardrobe": "casual henley-style knit top in a warm muted color",
        "background": "desaturated blue-gray studio interior with softly out-of-focus window panes behind her and diffused daylight",
    },
    {
        "name": "dusty-rose-vneck",
        "kind": "environment",
        "pose": "Relaxed three-quarter angle, her face fully toward the camera, with a subtle knowing smile",
        "wardrobe": "soft jersey top with a modest V neckline in a muted berry tone",
        "background": "softly blurred dressing-room interior with a dusty-rose wall, an indistinct curtain edge, and warm neutral decor, with broad low-contrast diffused beauty light and gentle frontal facial fill",
    },
    {
        "name": "soft-library-scoopneck",
        "kind": "environment",
        "pose": "Slight three-quarter angle with relaxed shoulders and a composed restrained smile",
        "wardrobe": "casual scoop-neck knit top in a soft neutral color",
        "background": "warm softly blurred library interior with indistinct shelving and a practical lamp, no readable text, with low-contrast beauty light",
    },
    {
        "name": "terracotta-crewneck",
        "kind": "environment",
        "pose": "Shoulders gently angled, her face fully toward the camera, with a relaxed open smile",
        "wardrobe": "casual fitted crew-neck knit top in a deep navy or plum tone",
        "background": "softly blurred contemporary interior with a terracotta accent wall, indistinct abstract wall art, and a warm practical lamp, with broad low-contrast diffused beauty light and gentle frontal facial fill",
    },
    {
        "name": "sunlit-loft-knit",
        "kind": "environment",
        "pose": "Relaxed three-quarter pose with a calm direct gaze and a slight smile",
        "wardrobe": "casual fine-knit top with a moderate rounded neckline in a muted cool tone",
        "background": "softly blurred sunlit loft interior with pale walls, a distant window, and an out-of-focus green plant",
    },
    {
        "name": "muted-lavender-jersey",
        "kind": "environment",
        "pose": "Square to camera with relaxed shoulders and a bright natural smile",
        "wardrobe": "casual short-sleeve jersey top in a charcoal or deep teal tone",
        "background": "softly blurred modern interior with a muted-lavender wall, an out-of-focus window edge, and indistinct decor, with broad low-contrast diffused beauty light and gentle frontal facial fill",
    },
    {
        "name": "warm-cafe-boatneck",
        "kind": "environment",
        "pose": "Subtle three-quarter angle with a friendly self-assured half-smile",
        "wardrobe": "casual boat-neck knit top in a rich warm color",
        "background": "warm softly blurred cafe interior with amber practical lights, no people, brands, or readable text",
    },
    {
        "name": "deep-teal-ribbed",
        "kind": "environment",
        "pose": "Chin slightly lowered, her face fully toward the camera, with a magnetic direct gaze",
        "wardrobe": "casual ribbed scoop-neck top in a warm ochre or soft cream tone",
        "background": "softly blurred lounge with a deep-teal wall, indistinct shelving, and a warm practical lamp, with broad low-contrast diffused beauty light and gentle frontal facial fill",
    },
    {
        "name": "gallery-wall-jersey",
        "kind": "environment",
        "pose": "Shoulders slightly angled with a poised warm expression",
        "wardrobe": "casual draped jersey top with a modest neckline in a muted jewel tone",
        "background": "softly blurred contemporary interior with indistinct abstract wall art and a warm practical lamp, no readable text",
    },
]
POSE_VARIANTS = tuple(style["pose"] for style in PORTRAIT_STYLES) + (
    "Square to camera with an easy closed-lip smile and relaxed shoulders",
    "Slightly angled toward the light with a calm thoughtful expression",
    "Chin gently raised with a poised direct gaze",
    "Relaxed contrapposto stance with a bright spontaneous smile",
    "Shoulders turned slightly away while her face returns fully to camera",
    "A subtle forward lean with an engaged friendly expression",
    "Standing tall with an assured neutral gaze and relaxed jaw",
    "Gentle head turn with a restrained mid-laugh expression",
    "One shoulder slightly lowered with a playful knowing smile",
    "Square to camera with a serene expression and softly parted lips",
    "Three-quarter stance with an alert direct gaze and subtle smile",
    "Relaxed upright posture with a composed warm expression",
)
WARDROBE_VARIANTS = tuple(style["wardrobe"] for style in PORTRAIT_STYLES) + (
    "fitted long-sleeve bateau-neck jersey top in burgundy",
    "sleeveless mock-neck knit top in deep teal",
    "soft wrap-style jersey top in cobalt blue",
    "square-neck ribbed knit top in muted plum",
    "simple fitted crew-neck tee in warm rust",
    "fine-knit cardigan over a modest scoop-neck top in soft neutrals",
    "short-sleeve knitted polo top in muted mustard",
    "asymmetrical-neckline knit top in charcoal",
    "soft cowl-neck jersey top in forest green",
    "short-sleeve mock-neck knit top in warm cream",
    "fitted waffle-knit henley top in wine red",
    "draped boat-neck top in muted sapphire",
    "relaxed chambray button-up shirt with casually rolled sleeves",
    "fitted cotton button-up blouse in a soft pastel color",
    "sleeveless ribbed tank top in a rich neutral tone",
    "fitted athletic tank top in a muted jewel color",
    "casual cropped crew-neck tee ending at the natural waist",
    "long-sleeve fitted crop top in a deep solid color",
    "soft pullover hoodie in heather gray",
    "cropped pullover hoodie in a muted berry tone",
    "lightweight zip hoodie over a fitted tank top",
    "relaxed crew-neck sweatshirt in forest green",
    "fine-knit fitted turtleneck in charcoal",
    "soft off-shoulder sweater in warm cream",
    "casual dark denim shirt with the collar open",
    "contrasting-sleeve baseball tee in muted colors",
    "soft gathered peasant blouse in a deep jewel tone",
    "sleeveless square-neck camisole under a lightweight open cardigan",
)
BACKGROUND_VARIANTS = tuple(style["background"] for style in PORTRAIT_STYLES) + (
    "bright contemporary kitchen with pale-blue cabinetry and softly blurred brass details",
    "backstage dressing room with softly glowing mirror lights and no readable text",
    "working art studio with blurred canvases, wood shelving, and diffused north light",
    "refined hotel lounge with deep-green upholstery and warm practical lamps",
    "sunlit conservatory with softly blurred leafy plants and pale stone",
    "converted brick loft with tall windows and warm neutral furnishings",
    "elegant theater foyer with softly blurred burgundy and brass details",
    "coastal apartment with a bright distant window and muted blue-gray decor",
    "independent bookstore interior with softly blurred shelves and amber lighting",
    "modern apartment with an ochre accent wall and sculptural neutral decor",
    "recording studio lounge with blurred acoustic panels and warm indirect light",
    "rooftop terrace at soft dusk with a defocused city background and gentle facial fill",
)


def distinct_style(index):
    cycle = index // len(POSE_VARIANTS)
    pose_index = index % len(POSE_VARIANTS)
    wardrobe_index = (index * 5 + cycle) % len(WARDROBE_VARIANTS)
    background_index = (index * 7 + cycle * 5) % len(BACKGROUND_VARIANTS)
    return {
        "name": f"p{pose_index + 1:02d}-w{wardrobe_index + 1:02d}-b{background_index + 1:02d}",
        "kind": "environment",
        "pose": POSE_VARIANTS[pose_index],
        "wardrobe": WARDROBE_VARIANTS[wardrobe_index],
        "background": BACKGROUND_VARIANTS[background_index],
    }


SEED_OFFSETS = (2_750_159, 31_415_927, 73_939_133, 141_421_357)
STAGE_SURNAMES = (
    "Ashford", "Bellamy", "Calder", "Delaney", "Everly",
    "Fairchild", "Holloway", "Marlowe", "Sinclair",
)
AGE_TARGETS = (
    21, 22, 23, 28,
    30, 31, 32, 38,
    24, 25, 26, 29,
    34, 35, 36, 39,
    42, 47, 27, 29,
)
BEAUTY_DIRECTIONS = (
    "charismatic, distinctive-looking",
    "classically elegant and poised",
    "fresh-faced, approachable, and highly photogenic",
    "striking and angular-featured",
    "softly featured and radiant",
    "glamorous with an editorial presence",
    "magnetic and unconventionally beautiful",
    "athletic, vibrant, and camera-ready",
    "refined and sophisticated",
    "warm, expressive, and naturally appealing",
    "bold with a high-fashion presence",
    "natural-looking and effortlessly photogenic",
    "romantic-featured and luminous",
    "statuesque with strong screen presence",
    "playful, lively, and expressive",
    "intense-featured and dramatically photogenic",
    "polished with classic leading-lady presence",
    "delicate-featured and ethereal",
    "confident, contemporary, and striking",
    "captivating, self-assured, and distinctive",
)
MAKEUP_DIRECTIONS = (
    "Understated editorial makeup with natural skin texture, softly defined eyes, and muted lips.",
    "Polished soft-glam makeup with luminous skin, defined lashes, and satin rose lips.",
    "Fresh camera-ready makeup with sheer coverage, softly flushed cheeks, and natural lips.",
    "Classic glamour makeup with subtle smoky eyes, sculpted cheeks, and rich satin lips.",
    "Modern editorial makeup with clean skin, graphic lashes, and a neutral matte lip.",
    "Warm-toned glamour makeup with bronze eyes, radiant skin, and softly glossy lips.",
    "Cool-toned polished makeup with defined brows, soft taupe eyes, and mauve lips.",
    "Minimal casting-headshot makeup with realistic skin, groomed brows, and tinted lips.",
    "Red-carpet makeup with softly contoured cheeks, defined eyes, and a polished berry lip.",
    "Natural luminous makeup with subtle eyeliner, peach blush, and a rose-neutral lip.",
    "High-fashion beauty makeup with sculpted eyes, clean highlights, and understated lips.",
    "Romantic makeup with soft lashes, rosy cheeks, and a translucent pink lip.",
    "Sophisticated matte makeup with precise brows, softly smoked eyes, and a nude lip.",
    "Sun-kissed camera makeup with warm blush, bronze lashes, and caramel-toned lips.",
    "Clean contemporary makeup with fresh skin, fine eyeliner, and softly satin lips.",
    "Dramatic but refined makeup with deep lashes, luminous cheeks, and wine-toned lips.",
    "Soft monochromatic makeup in warm rose tones with natural-looking skin texture.",
    "Elegant evening makeup with diffused smoky eyes, subtle contour, and classic red lips.",
    "Bright youthful makeup with clear skin, softly defined eyes, and a peach-gloss lip.",
    "Balanced camera-ready makeup with realistic pores, defined eyes, and neutral satin lips.",
)
EYE_SHAPES = (
    "almond-shaped", "large round", "deep-set", "softly hooded", "wide-set",
    "slightly close-set", "gently upturned", "slightly downturned", "large expressive", "narrow",
    "prominent", "small almond-shaped", "long-lashed", "heavy-lidded", "bright wide-set",
    "tapered", "rounded almond-shaped", "gently hooded", "sharp upturned", "softly deep-set",
)
BROW_SHAPES = (
    "thick straight brows", "fine arched brows", "full softly angled brows", "low straight brows",
    "high graceful arches", "bold dark brows", "soft natural brows", "wide-set curved brows",
    "delicate tapered brows", "strong angular brows", "gently rounded brows", "short full brows",
    "long elegant brows", "lightly feathered brows", "defined medium arches", "broad natural brows",
    "slender straight brows", "soft asymmetric brows", "prominent sculpted brows", "subtle low arches",
)
LIP_SHAPES = (
    "full balanced lips", "a fuller lower lip", "a pronounced cupid's bow", "small softly curved lips",
    "wide expressive lips", "narrow defined lips", "plush rounded lips", "a delicate upper lip",
    "slightly asymmetric lips", "heart-shaped lips", "broad satin lips", "compact full lips",
    "a softly bowed upper lip", "long gently curved lips", "naturally pouty lips", "thin elegant lips",
    "a full lower lip and fine upper lip", "softly downturned lips", "upturned smile-shaped lips",
    "medium softly defined lips",
)
SKIN_TONES = {
    "Caucasian": (
        "pale porcelain skin", "fair neutral skin", "fair cool-toned skin", "fair warm-toned skin",
        "fair freckled skin", "light olive skin", "lightly tanned skin", "peach-toned ivory skin",
    ),
    "Latin": (
        "light-medium golden skin", "warm olive skin", "lightly tanned skin", "golden-brown skin",
        "medium warm skin", "light olive skin", "caramel-toned skin", "warm beige skin",
    ),
    "Black": (
        "deep ebony-brown skin", "deep neutral-brown skin", "deep warm-brown skin",
        "medium-deep golden-brown skin", "rich dark-brown skin", "medium-deep neutral skin",
        "deep red-brown skin", "luminous dark-brown skin",
    ),
    "Asian": (
        "light neutral skin", "light-medium warm skin", "golden-beige skin", "pale cool-toned skin",
        "warm beige skin", "light olive-toned skin", "light peach-toned skin", "medium golden skin",
    ),
    "Mixed": (
        "medium golden-brown skin", "light-brown neutral skin", "warm beige-brown skin",
        "medium olive-brown skin", "caramel-toned skin", "light-medium golden skin",
        "medium warm-brown skin", "light olive skin",
    ),
    "Middle Eastern": (
        "light olive skin", "warm olive skin", "golden beige skin", "lightly tanned skin",
        "warm medium skin", "fair olive-toned skin", "caramel-beige skin", "deep olive skin",
    ),
}
EYE_COLORS = {
    "Caucasian": ("blue", "green", "gray", "hazel", "brown", "blue-gray", "green-gray", "amber-brown"),
    "Latin": ("brown", "dark brown", "hazel", "green", "amber", "warm brown", "gray-green", "honey-brown"),
    "Black": ("dark brown", "warm brown", "amber-brown", "hazel", "deep brown", "honey-brown", "black-brown", "golden-brown"),
    "Asian": ("dark brown", "warm brown", "deep brown", "black-brown", "amber-brown", "hazel-brown", "soft brown", "golden-brown"),
    "Mixed": ("brown", "hazel", "green-brown", "amber", "dark brown", "honey-brown", "gray-green", "warm brown"),
    "Middle Eastern": ("dark brown", "hazel", "green", "amber-brown", "warm brown", "gray-green", "honey-brown", "deep brown"),
}
BUST_VARIANTS = (
    ("a lean, athletic build with a small bust", "Small"),
    ("a fit, athletic build with a full bust", "Full"),
    ("a toned, athletic build with a medium bust", "Medium"),
    ("a curvy-athletic build with a large bust", "Large"),
    ("a strong, athletic build with a full bust", "Full"),
    ("a fit, athletic build with a large bust", "Large"),
    ("a lean, athletic build with a modest bust", "Small"),
    ("a sculpted, athletic build with a full bust", "Full"),
    ("a fit, balanced athletic build with a medium bust", "Medium"),
    ("a strong, curvy-athletic build with a large bust", "Large"),
    ("a toned, curvy-athletic build with a full bust", "Full"),
    ("a fit, curvy-athletic build with a large bust", "Large"),
    ("a toned, athletic build with a medium-full bust", "Medium"),
    ("a balanced, athletic build with a full bust", "Full"),
    ("a statuesque athletic build with a large bust", "Large"),
    ("a compact athletic build with a medium bust", "Medium"),
    ("a fit, statuesque build with a full bust", "Full"),
    ("a toned, statuesque build with a large bust", "Large"),
    ("a strong athletic build with a generously full bust", "Full"),
    ("a fit, curvy build with a prominently large bust", "Large"),
)
FEATURE_VARIANTS = (
    "a delicate ear cuff",
    "a tiny gold nose stud",
    "soft freckles across her nose",
    "a distinctive cheek dimple",
    "stacked silver ear piercings",
    "a fine-line shoulder tattoo",
    "a subtle eyebrow piercing",
    "a graceful beauty mark",
    "a small geometric wrist tattoo",
    "a pronounced cupid's bow",
    "a subtle cleft chin",
    "a beauty mark below her left eye",
    "a small polished hoop nose ring",
    "a fine-line collarbone tattoo",
    "multiple small gold ear piercings",
    "a delicate helix piercing",
    "a slight natural gap between her front teeth",
    "faint freckles across her cheeks",
    "a subtle eyebrow scar",
    "a tiny star tattoo behind one ear",
    "strong naturally arched eyebrows",
    "a softly fuller lower lip",
    "a subtly asymmetric smile",
    "a tiny beauty mark near her jawline",
    "a faint healed scar through one eyebrow",
    "a delicate double-helix piercing",
    "a slender polished septum ring",
    "a small fine-line tattoo at the nape of her neck",
    "scattered freckles near her temples",
    "a subtle polished lip stud",
    "a tiny hoop through one nostril",
    "a pronounced dimple in her left cheek",
    "a pronounced dimple in her right cheek",
    "a small beauty mark below her collarbone",
    "naturally thick, defined eyebrows",
)
FACE_SHAPES = (
    "a balanced oval face",
    "a softly rounded oval face with a tapered lower face",
    "a heart-shaped face",
    "a long elegant face",
    "a square face",
    "a soft diamond-shaped face",
    "a broad oval face with defined lower contours",
    "a narrow face",
    "a compact oval face with softly curved contours",
    "a balanced oblong face",
    "a pear-shaped face with a defined lower contour",
    "an inverted-triangle face with softened angles",
    "a petite oval face",
    "an elongated heart-shaped face",
    "a softly angular oval face",
    "a refined diamond-shaped face",
    "a rectangular face with softened corners",
    "a graceful triangular face",
    "a sculpted oval face",
    "a gently elongated oval face",
    "a softly rectangular face",
    "a refined kite-shaped face",
    "a broad heart-shaped face",
    "a narrow oval face with a gently wider forehead",
    "a short oval face with a defined lower contour",
    "a long diamond-shaped face",
    "a softly squared oval face",
    "a tapered oval face",
    "a broad oblong face",
    "a petite heart-shaped face",
    "an angular oblong face",
    "a softly rounded diamond-shaped face",
    "a classic symmetrical oval face",
    "a narrow heart-shaped face",
    "a strong trapezoidal face with softened corners",
)
NOSE_SHAPES = (
    "a straight narrow nose",
    "a small upturned nose",
    "a delicate straight nose",
    "an aquiline nose",
    "a broad straight nose",
    "a petite button nose",
    "a gently curved nose",
    "a distinctive Roman nose",
    "a short straight nose",
    "a slightly wide nose",
    "a slender slightly concave nose",
    "a prominent straight nose",
    "a softly rounded nose",
    "a narrow bridge with a refined tip",
    "a wider bridge with a rounded tip",
    "a gently hooked nose",
    "a short subtly upturned nose",
    "a long straight nose",
    "a softly flared nose",
    "a characterful slightly asymmetric nose",
    "a straight medium-width nose",
    "a slim nose with a high bridge",
    "a low gentle nasal bridge",
    "a softly bulbous nasal tip",
    "a refined Greek-profile nose",
    "a petite celestial nose",
    "a broad bridge with a softly tapered tip",
    "a long aquiline nose",
    "a short softly rounded nose",
    "a nose with a subtle dorsal curve",
    "a narrow nose with a gently lifted tip",
    "a wide-set nasal bridge",
    "a delicate gently hooked nose",
    "a strong Roman-profile nose",
    "a softly asymmetric button nose",
)
CHEEKBONE_SHAPES = (
    "high cheekbones",
    "softly contoured cheeks",
    "prominent cheekbones",
    "sculpted cheekbones",
    "angular cheekbones",
    "high wide cheekbones",
    "low subtle cheekbones",
    "sharp cheekbones",
    "gently rounded cheeks",
    "subtle cheekbones",
    "softly hollowed cheeks",
    "broad prominent cheekbones",
    "a delicate cheek contour",
    "pronounced malar cheekbones",
    "smooth gently lifted cheeks",
    "softly angular cheeks",
    "high narrow cheekbones",
    "broad soft cheekbones",
    "defined mid-height cheekbones",
    "an understated cheek contour",
    "high sculpted malar cheekbones",
    "soft flat cheek contours",
    "pronounced lateral cheekbones",
    "delicate high-set cheeks",
    "gently rounded upper cheeks",
    "lean contoured cheeks",
    "wide cheekbones with gentle slopes",
    "low defined cheek contours",
    "subtle apple-cheek contours",
    "an angular midface contour",
    "smooth high-set cheeks",
    "narrow prominent cheekbones",
    "balanced soft cheek contours",
    "a strong lateral cheek contour",
    "gently hollowed mid-cheeks",
)
JAW_AND_CHIN_SHAPES = (
    "a softly defined chin",
    "a softly tapered jaw and rounded chin",
    "a tapered chin",
    "a strong defined chin",
    "a firm jawline",
    "a pointed chin",
    "a smooth defined jaw",
    "a defined angular jawline",
    "a small chin",
    "a strong rounded chin",
    "a delicate V-shaped jawline",
    "a broad jaw with a tapered chin",
    "a softly squared jaw and delicate chin",
    "a narrow jaw and rounded chin",
    "a sculpted jawline and balanced chin",
    "a gently angular jaw and short chin",
    "a long tapered jaw",
    "a softly curved jawline and defined chin",
    "a wide defined jaw and subtle chin",
    "a graceful jawline and slightly cleft chin",
    "a tapered jaw with a softly squared chin",
    "a compact jaw with a defined chin",
    "a broad angular jaw and balanced chin",
    "a delicate rounded jawline",
    "a long sculpted jaw and refined chin",
    "a soft V-shaped jawline",
    "a sturdy jaw with a rounded chin",
    "a narrow angular jawline",
    "a balanced jaw with a subtle cleft chin",
    "a wide jaw with a pointed chin",
    "a gently tapered jaw with a small rounded chin",
    "a pronounced jaw with a square chin",
    "a fine jawline with an elongated chin",
    "a soft rectangular jaw with a balanced chin",
    "a gracefully curved jaw with a tapered chin",
)
HAIR_VARIANTS = {
    "Black": (
        ("long glossy jet-black waves", "Black"),
        ("a sleek glossy-black shoulder-length lob", "Black"),
        ("voluminous black natural curls", "Black"),
        ("a smooth jet-black jaw-length bob", "Black"),
        ("long straight raven-black hair", "Black"),
        ("a glossy black chin-length bob", "Black"),
        ("shoulder-length glossy black curls", "Black"),
        ("long black braids", "Black"),
        ("neat black cornrows gathered into a low ponytail", "Black"),
        ("soft black waves with face-framing layers", "Black"),
        ("a polished blunt black lob", "Black"),
        ("waist-length straight raven-black hair", "Black"),
        ("shoulder-length soft black waves", "Black"),
        ("a smooth rounded black bob", "Black"),
        ("defined shoulder-length black coils", "Black"),
        ("long rope twists in natural black", "Black"),
        ("a glossy black layered lob with curtain bangs", "Black"),
        ("long black curls with a center part", "Black"),
        ("a sleek asymmetrical black bob", "Black"),
        ("polished black waves swept behind one ear", "Black"),
    ),
    "Brunette": (
        ("long chestnut-brown layers", "Brunette"),
        ("a sleek espresso-brown bob", "Brunette"),
        ("voluminous chocolate-brown waves", "Brunette"),
        ("a glossy dark-brown chin-length cut", "Brunette"),
        ("long rich brunette curls", "Brunette"),
        ("a warm-brown shoulder-length lob", "Brunette"),
        ("soft deep-brown waves", "Brunette"),
        ("long straight deep-brown hair", "Brunette"),
        ("a polished chestnut layered cut", "Brunette"),
        ("dark-brown curls with subtle caramel ribbons", "Brunette"),
        ("long mocha-brown waves", "Brunette"),
        ("a glossy chestnut blunt lob", "Brunette"),
        ("shoulder-length warm chestnut curls", "Brunette"),
        ("a smooth espresso-brown rounded bob", "Brunette"),
        ("long chocolate-brown layers with curtain bangs", "Brunette"),
        ("polished deep cocoa-brown waves", "Brunette"),
        ("a sleek medium-brown asymmetrical lob", "Brunette"),
        ("long warm-brown curls", "Brunette"),
        ("a refined chestnut shoulder-length cut", "Brunette"),
        ("soft brunette waves with caramel face-framing sections", "Brunette"),
    ),
    "Blonde": (
        ("long golden-blonde waves", "Blonde"),
        ("a polished honey-blonde lob", "Blonde"),
        ("shoulder-length sandy-blonde layers", "Blonde"),
        ("a smooth warm-blonde short bob", "Blonde"),
        ("long champagne-blonde waves", "Blonde"),
        ("a soft wheat-blonde bob", "Blonde"),
        ("voluminous dark-blonde curls", "Blonde"),
        ("a polished beige-blonde layered cut", "Blonde"),
        ("warm blonde waves with darker roots", "Blonde"),
        ("long light-golden-blonde layers", "Blonde"),
        ("long pale-golden waves", "Blonde"),
        ("a glossy honey-blonde bob", "Blonde"),
        ("shoulder-length dark-blonde curls", "Blonde"),
        ("a smooth champagne-blonde rounded lob", "Blonde"),
        ("long honey-blonde layers with curtain bangs", "Blonde"),
        ("polished warm-golden-blonde waves", "Blonde"),
        ("a sleek sandy-blonde asymmetrical lob", "Blonde"),
        ("long warm-blonde curls", "Blonde"),
        ("a refined wheat-blonde shoulder-length cut", "Blonde"),
        ("soft golden-blonde waves with darker blonde ribbons", "Blonde"),
    ),
    "Red": (
        ("long copper-red waves", "Red"),
        ("deep auburn shoulder-length curls", "Red"),
        ("a glossy dark-red blunt bob", "Red"),
        ("shoulder-length polished copper curls", "Red"),
        ("auburn waves with a bold copper streak", "Red"),
        ("long burgundy-red curls", "Red"),
        ("a warm strawberry-red lob", "Red"),
        ("deep auburn hair with a vivid pink face-framing section", "Red"),
        ("dark-cherry waves with subtle cobalt-blue ends", "Red"),
        ("chestnut-red waves with copper ribbons", "Red"),
        ("long vivid copper curls", "Red"),
        ("a copper-red blunt lob with rose-pink ends", "Red"),
        ("shoulder-length dark-cherry waves", "Red"),
        ("a smooth auburn rounded bob", "Red"),
        ("long dark-auburn layers with vivid pink ends", "Red"),
        ("polished ruby-red waves", "Red"),
        ("a sleek deep-burgundy lob with cobalt-blue sections", "Red"),
        ("long cinnamon-red curls", "Red"),
        ("a refined copper shoulder-length cut", "Red"),
        ("soft dark-auburn hair with blue face-framing sections", "Red"),
    ),
}


def distinct_profile(profile, profile_number, variant):
    result = dict(profile)
    first_name = profile["name"].split()[0]
    global_index = profile_number * 10 + variant
    hair_family = ("Blonde", "Brunette", "Black", "Red")[(profile_number * 10 + variant) % 4]
    hair_options = HAIR_VARIANTS[hair_family]
    if profile["ethnicity"] not in {"Black", "Mixed"}:
        hair_options = tuple(
            option for option in hair_options
            if not any(term in option[0].lower() for term in ("braid", "twist", "coil", "cornrow"))
        )
    hair_index = (variant * 7 + profile_number * 3) % len(hair_options)
    hair, hair_group = hair_options[hair_index]
    build, bust_group = BUST_VARIANTS[(global_index * 9 + profile_number * 7) % len(BUST_VARIANTS)]
    face = ", ".join((
        FACE_SHAPES[(variant + profile_number * 3) % len(FACE_SHAPES)],
        NOSE_SHAPES[(variant * 3 + profile_number) % len(NOSE_SHAPES)],
        CHEEKBONE_SHAPES[(variant * 7 + profile_number * 2) % len(CHEEKBONE_SHAPES)],
        JAW_AND_CHIN_SHAPES[(variant * 9 + profile_number * 7) % len(JAW_AND_CHIN_SHAPES)],
    ))
    appearance = ", ".join((
        SKIN_TONES[profile["ethnicity"]][(global_index * 3 + profile_number * 5) % 8],
        f"{EYE_SHAPES[(global_index * 7 + profile_number * 3) % len(EYE_SHAPES)]} "
        f"{EYE_COLORS[profile['ethnicity']][(global_index * 5 + profile_number * 3) % 8]} eyes",
        BROW_SHAPES[(global_index * 11 + profile_number * 7) % len(BROW_SHAPES)],
        LIP_SHAPES[(global_index * 13 + profile_number * 9) % len(LIP_SHAPES)],
    ))
    result.update({
        "slug": f"{profile['slug']}-d{variant + 1:02d}",
        "name": profile["name"] if variant == 0 else f"{first_name} {STAGE_SURNAMES[variant - 1]}",
        "age": AGE_TARGETS[global_index % len(AGE_TARGETS)],
        "appearance": appearance,
        "hair": hair,
        "hair_group": hair_group,
        "feature": FEATURE_VARIANTS[(variant * 3 + profile_number * 7) % len(FEATURE_VARIANTS)],
        "face": face,
        "build": build,
        "build_group": "Athletic",
        "bust_group": bust_group,
        "beauty_direction": BEAUTY_DIRECTIONS[(global_index * 7 + profile_number * 3) % len(BEAUTY_DIRECTIONS)],
        "makeup": MAKEUP_DIRECTIONS[(global_index * 11 + profile_number * 5) % len(MAKEUP_DIRECTIONS)],
        "omit_prompt_name": True,
    })
    return result


def identity_seed(slug):
    return int.from_bytes(hashlib.blake2b(slug.encode(), digest_size=8).digest(), "big") % 8_000_000_000_000_000_000


PERFORMERS = [
    (profile, candidate, profile_number * 1_000_000_000 + SEED_OFFSETS[candidate - 1], PORTRAIT_STYLES[(profile_number + candidate - 2) % len(PORTRAIT_STYLES)])
    for profile_number, profile in enumerate(PERFORMER_PROFILES, 1)
    for candidate in range(1, 5)
]
assert len(PERFORMERS) == 96
EXPANSION = [
    (profile, candidate, profile_number * 1_000_000_000 + SEED_OFFSETS[candidate - 1], PORTRAIT_STYLES[(profile_number * 2 + candidate - 3) % len(PORTRAIT_STYLES)])
    for profile_number, profile in enumerate(EXPANSION_PROFILES, 1)
    for candidate in range(1, 5)
]
assert len(EXPANSION) == 200
assert all(
    Counter(style["kind"] for _, _, _, style in EXPANSION[offset:offset + 4]) == {"environment": 4}
    for offset in range(0, len(EXPANSION), 4)
)
DISTINCT_PROFILES = [
    distinct_profile(profile, profile_number, variant)
    for profile_number, profile in enumerate(EXPANSION_PROFILES)
    for variant in range(10)
]
DISTINCT_EXPANSION = [
    (
        profile,
        1,
        identity_seed(profile["slug"]),
        distinct_style(profile_number - 1),
    )
    for profile_number, profile in enumerate(DISTINCT_PROFILES, 1)
]
assert len(DISTINCT_EXPANSION) == len({profile["slug"] for profile in DISTINCT_PROFILES}) == 500
assert len({profile["name"] for profile in DISTINCT_PROFILES}) == 500
assert not any("gray hair" in profile["hair"].lower() for profile in DISTINCT_PROFILES)
assert not any(
    term in profile["hair"].lower()
    for profile in DISTINCT_PROFILES
    for term in ("pixie", "blue-black")
)
assert not any(
    any(term in profile["hair"].lower() for term in ("braid", "twist", "coil", "cornrow"))
    for profile in DISTINCT_PROFILES
    if profile["ethnicity"] not in {"Black", "Mixed"}
)
assert Counter(profile["hair_group"] for profile in DISTINCT_PROFILES) == {
    "Blonde": 125, "Brunette": 125, "Black": 125, "Red": 125,
}
assert Counter(profile["bust_group"] for profile in DISTINCT_PROFILES) == {
    "Small": 52, "Medium": 99, "Full": 168, "Large": 181,
}
assert Counter(profile["age"] // 10 * 10 for profile in DISTINCT_PROFILES) == {
    20: 250, 30: 200, 40: 50,
}
assert all(
    len({profile["face"] for profile in DISTINCT_PROFILES[offset:offset + 10]}) == 10
    for offset in range(0, 500, 10)
)
assert len({profile["appearance"] for profile in DISTINCT_PROFILES}) > 400
assert len({profile["beauty_direction"] for profile in DISTINCT_PROFILES}) == len(BEAUTY_DIRECTIONS)
assert len({profile["makeup"] for profile in DISTINCT_PROFILES}) == len(MAKEUP_DIRECTIONS)
assert len({seed for _, _, seed, _ in DISTINCT_EXPANSION}) == 500
assert len({style["name"] for _, _, _, style in DISTINCT_EXPANSION}) > 400
PILOT_PROFILES = [
    (PERFORMER_PROFILES[3], "Subtle three-quarter angle with relaxed shoulders, her face fully toward the camera, and a calm direct gaze", "emerald-green short-sleeve knit top with a moderate square neckline"),
    (PERFORMER_PROFILES[6], "Shoulders slightly angled, her face fully toward the camera, with a composed soft expression", "rust-colored crew-neck ribbed knit top"),
    (PERFORMER_PROFILES[7], "Subtle three-quarter angle, her face fully toward the camera, with a relaxed genuine smile", "cobalt-blue short-sleeve crew-neck knit top"),
    (PERFORMER_PROFILES[10], "Shoulders slightly angled, her face fully toward the camera, with a poised neutral expression", "jade-green crew-neck knit top"),
]
PILOT = [
    (profile, pose, wardrobe, candidate, profile_number * 100_000_000 + seed_offset)
    for profile_number, (profile, pose, wardrobe) in enumerate(PILOT_PROFILES, 1)
    for candidate, seed_offset in enumerate((104_729, 2_750_159, 15_485_863), 1)
]
assert len(PILOT) == 12


def performer_prompt(profile, photography=False):
    finish = (
        " Clean studio beauty lighting, warm neutral background, eye-level 85mm portrait lens, "
        "fine natural pores, and smooth tonal gradients."
        if photography else ""
    )
    return (
        f"Studio portrait of {profile['name']}, a strikingly beautiful fictional {profile['age']}-year-old "
        f"{profile['identity']}, with {profile['appearance']}, {profile['hair']}, and {profile['feature']}. "
        f"{profile['pose']}. Waist-up composition; she has {profile['build']} and wears a fitted "
        f"{profile['wardrobe']}. Camera-ready glamour makeup with softly smoky eyes, defined lashes, "
        f"sculpted cheekbones, radiant skin, and satin lips.{finish}"
    )


def pilot_prompt(profile, pose, wardrobe, photography=False):
    finish = (
        " Clean studio beauty lighting, warm neutral background, eye-level 85mm portrait lens, "
        "fine natural pores, and smooth tonal gradients."
        if photography else ""
    )
    return (
        f"Studio portrait of {profile['name']}, a strikingly beautiful, youthful-looking fictional "
        f"{profile['age']}-year-old {profile['identity']}, with {profile['appearance']}, "
        f"{profile['hair']}, and {profile['feature']}. {pose}. Waist-up composition, wearing a casual "
        f"{wardrobe}. Polished camera-ready makeup suited to her complexion, with defined eyes, "
        f"luminous natural-looking skin, and satin lips.{finish}"
    )


def generation_prompt(profile, style, age_emphasis=False, background=None, age_wording="exact", hair=None):
    age = profile["age"]
    beauty = profile.get("beauty_direction", "strikingly beautiful, conventionally attractive")
    beauty = f"{'an' if beauty[0].lower() in 'aeiou' else 'a'} {beauty}"
    decade = {
        20: "twenties", 30: "thirties", 40: "forties", 50: "fifties", 60: "sixties",
    }[age // 10 * 10]
    period = "early" if age % 10 <= 3 else "mid" if age % 10 <= 6 else "late"
    if age_wording == "band":
        age_direction = (
            "with a youthful adult appearance and fresh well-rested features"
            if age < 35 else
            "with a vibrant, well-rested contemporary appearance appropriate to her age band"
        )
        subject = (
            f"{beauty} fictional {profile['identity']} "
            f"in her {period} {decade}, {age_direction}"
        )
    elif age_wording == "vague":
        subject = (
            f"{beauty}, fresh and contemporary fictional "
            f"adult {profile['identity']}"
        )
    else:
        age_direction = (
            "glamorous and vibrant at her stated age, with smooth healthy skin and no exaggerated aging"
            if age >= 40 else "polished and contemporary at her stated age"
        )
        subject = (
            f"{beauty} fictional {age}-year-old "
            f"{profile['identity']}, {age_direction}"
        )
    details = ", ".join(filter(None, (profile["appearance"], profile.get("face"), hair or profile["hair"])))
    name = "" if profile.get("omit_prompt_name") else f"{profile['name']}, "
    makeup = profile.get(
        "makeup",
        "Polished camera-ready makeup suited to her complexion, with defined eyes, "
        "luminous natural-looking skin, and satin lips.",
    )
    composition = style.get("composition", "Waist-up composition")
    prompt = (
        f"Studio portrait of {name}{subject}. She has {details}, and {profile['feature']}. "
        f"{style['pose']}. {composition}; she has {profile['build']} and wears a "
        f"{style['wardrobe']}, against a {background or style['background']}. {makeup}"
    )
    prompt += profile.get("prompt_suffix", "")
    if age_emphasis:
        appearance = (
            "fresh adult features and smooth natural skin"
            if age < 35 else
            "a vibrant, well-rested appearance and healthy natural skin"
            if age < 45 else
            "an age-authentic, vibrant appearance and healthy natural skin texture"
        )
        prompt += f" She is an actress in her {period} {decade}, with {appearance}."
    return prompt


GEOGRAPHIC_IDENTITIES = {
    "Caucasian": (
        "Swedish actress of Swedish heritage", "Norwegian actress of Norwegian heritage",
        "Danish actress of Danish heritage", "Finnish actress of Finnish heritage",
        "Icelandic actress of Icelandic heritage", "Irish actress of Irish heritage",
        "Scottish actress of Scottish heritage", "French actress of French heritage",
        "Dutch actress of Dutch heritage", "German actress of German heritage",
        "Austrian actress of Austrian heritage", "Swiss actress of Swiss heritage",
        "Polish actress of Polish heritage", "Czech actress of Czech heritage",
        "Ukrainian actress of Ukrainian heritage", "Spanish actress of Spanish heritage",
        "Portuguese actress of Portuguese heritage", "Italian actress of Italian heritage",
        "Greek actress of Greek heritage", "Croatian actress of Croatian heritage",
    ),
    "Latin": (
        "Brazilian actress of Brazilian heritage", "Colombian actress of Colombian heritage",
        "Argentine actress of Argentine heritage", "Mexican actress of Mexican heritage",
        "Chilean actress of Chilean heritage", "Cuban actress of Cuban heritage",
    ),
    "Black": (
        "Ghanaian actress of Akan heritage", "Nigerian actress of Yoruba heritage",
        "Kenyan actress of Kenyan heritage", "South African actress of Zulu heritage",
        "Jamaican actress of Afro-Jamaican heritage", "British actress of Nigerian heritage",
    ),
    "Asian": (
        "Japanese actress of Japanese heritage", "Korean actress of Korean heritage",
        "Chinese actress of Chinese heritage", "Vietnamese actress of Vietnamese heritage",
        "Filipino actress of Filipino heritage", "Thai actress of Thai heritage",
    ),
    "Mixed": (
        "British actress of mixed Nigerian and English heritage",
        "Brazilian actress of mixed Afro-European Brazilian heritage",
        "French actress of mixed French and Senegalese heritage",
        "South African actress of mixed South African heritage",
        "Dutch actress of mixed Dutch and Indonesian heritage",
    ),
    "Middle Eastern": (
        "Lebanese actress of Lebanese heritage", "Turkish actress of Turkish heritage",
        "Iranian actress of Iranian heritage", "Syrian actress of Syrian heritage",
        "Jordanian actress of Jordanian heritage", "Egyptian actress of Egyptian heritage",
    ),
}
REFINED_BEAUTY = (
    "charismatic and distinctive-looking", "classically elegant and poised",
    "fresh-faced, approachable, and photogenic", "softly featured and radiant",
    "warm, expressive, and naturally appealing", "natural-looking and effortlessly photogenic",
    "athletic, vibrant, and camera-ready", "confident, contemporary, and striking",
)
REFINED_MAKEUP = (
    "Understated camera-ready makeup with visible natural skin texture, softly defined eyes, and muted lips.",
    "Understated camera-ready makeup with visible natural skin texture, softly defined eyes, and muted lips.",
    "Fresh natural makeup with sheer coverage, softly flushed cheeks, and lightly tinted lips.",
    "Clean contemporary makeup with natural-looking skin, fine eyeliner, and softly satin lips.",
)
REFINED_EYES = (
    "almond-shaped", "softly rounded", "gently hooded", "slightly upturned",
    "slightly downturned", "deep-set", "wide-set", "large expressive",
)
REFINED_BROWS = (
    "soft natural brows", "gently arched brows", "medium straight brows", "lightly feathered brows",
    "subtle rounded brows", "defined natural brows", "fine tapered brows", "softly angled brows",
)
REFINED_LIPS = (
    "balanced natural lips", "a softly defined cupid's bow", "medium gently curved lips",
    "a subtly fuller lower lip", "softly rounded lips", "wide natural lips",
    "fine elegant lips", "gently bow-shaped lips",
)
REFINED_FEATURES = (
    "a delicate ear cuff", "a tiny gold nose stud", "soft freckles across her nose",
    "a tiny beauty mark near her jawline", "stacked silver ear piercings", "a fine-line shoulder tattoo",
    "a graceful beauty mark", "a subtle eyebrow scar",
)
DIRECT_POSES = (
    "Square to camera with relaxed shoulders and a calm direct gaze",
    "Subtle three-quarter stance with her face fully toward the camera and a soft smile",
    "Shoulders gently angled while her eyes look directly into the camera",
    "Relaxed upright posture with a warm direct gaze and a slight smile",
    "Chin slightly lowered, face fully visible, with a confident direct gaze",
    "One shoulder slightly lowered while her face and eyes remain directed at the camera",
)
REFINEMENT_PILOT_INDICES = (0, 27, 66, 89, 175, 213, 227, 239, 342, 398, 406, 417, 423, 465, 498)


def refined_prompt(index):
    profile, _, _, style = DISTINCT_EXPANSION[index]
    profile = dict(profile)
    base, variant = divmod(index, 10)
    secondary_features = (
        NOSE_SHAPES[(variant * 3 + base) % len(NOSE_SHAPES)],
        CHEEKBONE_SHAPES[(variant * 7 + base * 2) % len(CHEEKBONE_SHAPES)],
        JAW_AND_CHIN_SHAPES[(variant * 9 + base * 7) % len(JAW_AND_CHIN_SHAPES)],
    )
    skin = profile["appearance"].split(", ", 1)[0]
    eye_color = EYE_COLORS[profile["ethnicity"]][(index * 5 + base * 3) % 8]
    profile.update({
        "identity": GEOGRAPHIC_IDENTITIES[profile["ethnicity"]][index % len(GEOGRAPHIC_IDENTITIES[profile["ethnicity"]])],
        "age": min(profile["age"], 34),
        "appearance": f"{skin}, {REFINED_EYES[index % len(REFINED_EYES)]} {eye_color} eyes",
        "face": ", ".join((
            FACE_SHAPES[(variant + base * 3) % len(FACE_SHAPES)],
            secondary_features[index % len(secondary_features)],
        )),
        "beauty_direction": REFINED_BEAUTY[index % len(REFINED_BEAUTY)],
        "makeup": REFINED_MAKEUP[index % len(REFINED_MAKEUP)],
    })
    style = dict(style, pose=DIRECT_POSES[index % len(DIRECT_POSES)])
    return generation_prompt(profile, style, age_wording="band") + (
        " Photorealistic portrait photography with realistic adult facial proportions, "
        "fine natural pores, subtle skin texture, and lifelike hair detail."
    )


REFINEMENT_PILOT = tuple(
    entry
    for pair, index in enumerate(REFINEMENT_PILOT_INDICES, 1)
    for entry in (
        (
            f"p{pair:02d}-current-{DISTINCT_PROFILES[index]['slug']}",
            generation_prompt(DISTINCT_PROFILES[index], DISTINCT_EXPANSION[index][3], age_wording="band"),
            DISTINCT_EXPANSION[index][2],
        ),
        (
            f"p{pair:02d}-refined-{DISTINCT_PROFILES[index]['slug']}",
            refined_prompt(index),
            DISTINCT_EXPANSION[index][2],
        ),
    )
)
assert len(REFINEMENT_PILOT) == 30
assert all(REFINEMENT_PILOT[index][2] == REFINEMENT_PILOT[index + 1][2] for index in range(0, 30, 2))

FACE_COMBINATION_STRATEGIES = (
    "full-current", "full-soft", "structure-only", "balanced", "minimal", "natural-prose",
)
FACE_COMBINATION_INDICES = (0, 89, 175, 213, 342, 406, 417, 465)


def face_combination_prompt(index, strategy):
    profile, _, _, style = DISTINCT_EXPANSION[index]
    profile = dict(profile)
    base, variant = divmod(index, 10)
    face_parts = (
        FACE_SHAPES[(variant + base * 3) % len(FACE_SHAPES)],
        NOSE_SHAPES[(variant * 3 + base) % len(NOSE_SHAPES)],
        CHEEKBONE_SHAPES[(variant * 7 + base * 2) % len(CHEEKBONE_SHAPES)],
        JAW_AND_CHIN_SHAPES[(variant * 9 + base * 7) % len(JAW_AND_CHIN_SHAPES)],
    )
    skin = profile["appearance"].split(", ", 1)[0]
    eye_color = EYE_COLORS[profile["ethnicity"]][(index * 5 + base * 3) % 8]
    eyes = f"{REFINED_EYES[index % len(REFINED_EYES)]} {eye_color} eyes"
    soft_appearance = ", ".join((
        skin, eyes, REFINED_BROWS[index % len(REFINED_BROWS)], REFINED_LIPS[index % len(REFINED_LIPS)],
    ))
    profile.update({
        "identity": GEOGRAPHIC_IDENTITIES[profile["ethnicity"]][index % len(GEOGRAPHIC_IDENTITIES[profile["ethnicity"]])],
        "age": min(profile["age"], 34),
        "beauty_direction": REFINED_BEAUTY[index % len(REFINED_BEAUTY)],
        "makeup": REFINED_MAKEUP[index % len(REFINED_MAKEUP)],
        "feature": REFINED_FEATURES[index % len(REFINED_FEATURES)],
    })
    if strategy == "full-soft":
        profile["appearance"] = soft_appearance
    elif strategy == "structure-only":
        profile["appearance"] = f"{skin}, {eye_color} eyes"
    elif strategy == "balanced":
        profile["appearance"] = soft_appearance
        profile["face"] = ", ".join((face_parts[0], face_parts[1], face_parts[2 + index % 2]))
    elif strategy == "minimal":
        profile["appearance"] = f"{skin}, {eyes}"
        profile["face"] = ", ".join((face_parts[0], face_parts[1 + index % 3]))
    elif strategy == "natural-prose":
        profile["appearance"] = soft_appearance
        profile["face"] = (
            f"{face_parts[0]}, naturally balanced with {face_parts[1]}, "
            f"{face_parts[2]}, and {face_parts[3]}"
        )
    elif strategy != "full-current":
        raise ValueError(f"unknown face-combination strategy: {strategy}")
    style = dict(style, pose=DIRECT_POSES[index % len(DIRECT_POSES)])
    return generation_prompt(profile, style, age_wording="band") + (
        " Photorealistic portrait photography with realistic adult facial proportions, "
        "fine natural pores, subtle skin texture, and lifelike hair detail."
    )


FACE_COMBINATION_PILOT = tuple(
    (
        f"g{group:02d}-s{strategy_number:02d}-{strategy}-{DISTINCT_PROFILES[index]['slug']}",
        face_combination_prompt(index, strategy),
        DISTINCT_EXPANSION[index][2],
    )
    for group, index in enumerate(FACE_COMBINATION_INDICES, 1)
    for strategy_number, strategy in enumerate(FACE_COMBINATION_STRATEGIES, 1)
)
assert len(FACE_COMBINATION_PILOT) == 48
assert all(
    len({entry[2] for entry in FACE_COMBINATION_PILOT[offset:offset + 6]}) == 1
    for offset in range(0, 48, 6)
)

PRODUCTION_POSES = DIRECT_POSES + (
    "Subtle three-quarter angle with relaxed shoulders, her face and eyes fully toward the camera",
    "Shoulders turned slightly left while her face returns to a confident direct gaze",
    "Shoulders turned slightly right while her face returns to a warm direct gaze",
    "Gentle head tilt with a relaxed closed-lip smile and direct eye contact",
    "Chin gently raised with poised direct eye contact and relaxed shoulders",
    "A slight forward lean with an engaged expression and eyes directly toward the camera",
    "Standing tall with an assured neutral expression and direct eye contact",
    "Square to camera with a bright spontaneous smile and relaxed shoulders",
    "Three-quarter stance with a subtle knowing smile and direct eye contact",
    "One shoulder slightly forward with a calm, self-possessed direct gaze",
    "Relaxed posture with chin slightly angled and eyes returning fully to the camera",
    "Shoulders gently lowered with a serene direct gaze and softly parted lips",
    "A restrained mid-laugh expression while keeping her eyes toward the camera",
    "Subtle contrapposto stance with her face fully visible and a friendly direct gaze",
    "Head held level with an intense but relaxed direct gaze",
    "A slight sideways lean with her face and eyes fully toward the camera",
    "Square to camera with one eyebrow subtly raised and a confident direct gaze",
    "Shoulders angled toward the light while maintaining direct eye contact",
)
PRODUCTION_BACKGROUNDS = BACKGROUND_VARIANTS + (
    "soft burgundy studio backdrop with broad diffused beauty light and a faint warm edge light",
    "dusty-blue studio backdrop with soft frontal light and gentle tonal falloff",
    "pale-sage studio backdrop with clean diffused light and subtle shadow depth",
    "warm ochre studio backdrop with broad soft light and muted golden fill",
    "muted lavender studio backdrop with cool diffused light and a soft neutral fill",
    "terracotta studio backdrop with warm beauty light and gentle facial fill",
    "charcoal-gray studio backdrop with soft directional light and a subtle rim light",
    "warm cream studio backdrop with airy high-key diffused lighting",
    "leafy city courtyard with softly blurred brick, greenery, and open shade",
    "bright ceramics studio with blurred shelving, pale clay forms, and north light",
    "modern library reading room with softly blurred bookshelves and warm table lamps",
    "cozy music rehearsal room with blurred instruments and warm indirect light",
    "minimal concrete gallery with distant abstract sculpture and soft window light",
    "greenhouse cafe with softly blurred plants, glass framing, and natural daylight",
    "vintage cinema lobby with blurred velvet, brass details, and warm practical lights",
    "contemporary home office with muted shelving, a distant window, and soft daylight",
    "pale sandstone terrace with softly blurred architecture and open shade",
    "elegant staircase hall with indistinct balustrades and warm window light",
    "colorful design studio with softly blurred material samples and diffused daylight",
    "quiet urban balcony at blue hour with a softly defocused city background",
    "quiet riverside promenade with softly blurred trees and open shade",
    "sunlit cobblestone side street with softly defocused warm facades",
    "leafy city park path with a distant bench and soft late-afternoon light",
    "coastal boardwalk with a softly blurred horizon and gentle overcast light",
    "rooftop garden with defocused greenery, pale stone, and soft daylight",
    "relaxed cafe terrace with blurred planters and warm morning light",
    "calm lakeside pier with a softly defocused shoreline and open shade",
    "brick courtyard with climbing greenery and broad diffused daylight",
)
PRODUCTION_WARDROBES = WARDROBE_VARIANTS + (
    "soft linen button-up shirt in muted sky blue with casually rolled sleeves",
    "fitted sleeveless polo knit in deep forest green",
    "ribbed scoop-neck tank top in warm terracotta",
    "casual cropped cardigan over a fitted camisole in coordinated neutral tones",
    "lightweight athletic quarter-zip top in muted cobalt",
    "soft V-neck cotton tee in deep wine red",
    "relaxed striped bateau-neck jersey top in navy and cream",
    "fitted square-neck long-sleeve top in dark plum",
    "casual sleeveless button-front blouse in muted teal",
    "fine-knit short-sleeve sweater in warm mustard",
    "simple crossover jersey top in charcoal blue",
    "lightweight bomber jacket over a fitted crew-neck tee in muted colors",
    "casual floral sundress with a moderate square neckline",
    "solid-color linen sundress with narrow shoulder straps",
    "fitted ribbed tube top in a rich jewel tone",
    "simple bandeau top under a lightweight cropped cardigan",
    "fitted halter-neck knit top in deep teal",
    "soft one-shoulder jersey top in muted berry",
    "satin-trim camisole in warm cream",
    "fitted racerback tank top in muted cobalt",
    "casual wrap dress with a moderate V neckline in dark plum",
    "denim sundress layered over a fitted short-sleeve tee",
    "soft sweetheart-neckline knit top in forest green",
    "simple strapless fitted top in warm terracotta",
    "relaxed plain cotton tee in a soft washed color",
    "casual open flannel overshirt over a fitted tank top",
    "simple ribbed long-sleeve crew-neck top in muted blue",
    "lightweight cropped zip hoodie over a plain camisole",
    "soft jersey sundress with a moderate scoop neckline",
    "fitted cotton tank top in warm rust",
    "casual athletic scoop-neck top in deep green",
    "relaxed sleeveless henley top in muted plum",
)
COMPOSITION_VARIANTS = (
    "Tight head-and-shoulders composition with the tops of her shoulders visible",
    "Head-and-upper-torso composition with modest space around her",
    "Chest-up composition framed just below the bust",
    "Mid-torso composition with some surrounding environment visible",
    "Waist-up composition with balanced headroom",
    "Slightly wider waist-up environmental composition",
)
PRODUCTION_AGES = (21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34)
PRODUCTION_FACE_STRATEGIES = ("balanced", "minimal", "natural-prose")


def production_style(index, slug):
    pose_index = identity_seed(f"{slug}:pose") % len(PRODUCTION_POSES)
    wardrobe_index = identity_seed(f"{slug}:wardrobe") % len(PRODUCTION_WARDROBES)
    background_index = identity_seed(f"{slug}:background") % len(PRODUCTION_BACKGROUNDS)
    composition_index = index % len(COMPOSITION_VARIANTS)
    return {
        "name": (
            f"p{pose_index + 1:02d}-w{wardrobe_index + 1:02d}-"
            f"b{background_index + 1:02d}-c{composition_index + 1:02d}"
        ),
        "kind": "environment",
        "pose": PRODUCTION_POSES[pose_index],
        "wardrobe": PRODUCTION_WARDROBES[wardrobe_index],
        "background": PRODUCTION_BACKGROUNDS[background_index],
        "composition": COMPOSITION_VARIANTS[composition_index],
    }


def production_profile(profile, index):
    result = dict(profile)
    slug = profile["slug"]
    base, variant = divmod(index, 10)
    face_parts = (
        FACE_SHAPES[(variant + base * 3) % len(FACE_SHAPES)],
        NOSE_SHAPES[(variant * 3 + base) % len(NOSE_SHAPES)],
        CHEEKBONE_SHAPES[(variant * 7 + base * 2) % len(CHEEKBONE_SHAPES)],
        JAW_AND_CHIN_SHAPES[(variant * 9 + base * 7) % len(JAW_AND_CHIN_SHAPES)],
    )
    skin = profile["appearance"].split(", ", 1)[0]
    eye_color = EYE_COLORS[profile["ethnicity"]][identity_seed(f"{slug}:eyes") % 8]
    eye_shape = REFINED_EYES[identity_seed(f"{slug}:eye-shape") % len(REFINED_EYES)]
    eyes = f"{eye_shape} {eye_color} eyes"
    soft_appearance = ", ".join((
        skin,
        eyes,
        REFINED_BROWS[identity_seed(f"{slug}:brows") % len(REFINED_BROWS)],
        REFINED_LIPS[identity_seed(f"{slug}:lips") % len(REFINED_LIPS)],
    ))
    strategy = PRODUCTION_FACE_STRATEGIES[index % len(PRODUCTION_FACE_STRATEGIES)]
    result.update({
        "identity": GEOGRAPHIC_IDENTITIES[profile["ethnicity"]][
            identity_seed(f"{slug}:identity") % len(GEOGRAPHIC_IDENTITIES[profile["ethnicity"]])
        ],
        "age": PRODUCTION_AGES[index % len(PRODUCTION_AGES)],
        "appearance": soft_appearance if strategy != "minimal" else f"{skin}, {eyes}",
        "beauty_direction": REFINED_BEAUTY[identity_seed(f"{slug}:beauty") % len(REFINED_BEAUTY)],
        "makeup": REFINED_MAKEUP[identity_seed(f"{slug}:makeup") % len(REFINED_MAKEUP)],
        "feature": REFINED_FEATURES[identity_seed(f"{slug}:feature") % len(REFINED_FEATURES)],
        "face_strategy": strategy,
        "prompt_suffix": (
            " Photorealistic portrait photography with realistic adult facial proportions, "
            "fine natural pores, subtle skin texture, and lifelike hair detail."
        ),
    })
    if strategy == "balanced":
        result["face"] = ", ".join((face_parts[0], face_parts[1], face_parts[2 + index % 2]))
    elif strategy == "minimal":
        result["face"] = ", ".join((face_parts[0], face_parts[1 + index % 3]))
    else:
        result["face"] = (
            f"{face_parts[0]}, naturally balanced with {face_parts[1]}, "
            f"{face_parts[2]}, and {face_parts[3]}"
        )
    return result


PRODUCTION_PROFILES = tuple(production_profile(profile, index) for index, profile in enumerate(DISTINCT_PROFILES))
PRODUCTION_EXPANSION = tuple(
    (
        profile,
        1,
        identity_seed(profile["slug"]),
        production_style(index, profile["slug"]),
    )
    for index, profile in enumerate(PRODUCTION_PROFILES)
)
assert len(PRODUCTION_EXPANSION) == 500
assert Counter(profile["face_strategy"] for profile in PRODUCTION_PROFILES) == {
    "balanced": 167, "minimal": 167, "natural-prose": 166,
}
assert Counter(profile["age"] // 10 * 10 for profile in PRODUCTION_PROFILES) == {20: 324, 30: 176}
assert min(Counter(style["composition"] for _, _, _, style in PRODUCTION_EXPANSION).values()) >= 83
assert len({style["name"] for _, _, _, style in PRODUCTION_EXPANSION}) > 490

CROSSED_IDENTITIES = (
    (
        "swedish", "Swedish actress of Swedish heritage",
        "fair cool-toned skin, almond-shaped blue-gray eyes, soft natural brows, balanced natural lips",
        "a balanced oval face, naturally balanced with a straight narrow nose, high cheekbones, and a softly tapered jaw",
    ),
    (
        "irish", "Irish actress of Irish heritage",
        "fair freckled skin, softly rounded green eyes, gently arched brows, a softly defined cupid's bow",
        "a heart-shaped face, naturally balanced with a small upturned nose, gently rounded cheeks, and a delicate chin",
    ),
    (
        "french", "French actress of French heritage",
        "light olive skin, gently hooded hazel eyes, fine tapered brows, medium gently curved lips",
        "a long oblong face, naturally balanced with an aquiline nose, low subtle cheekbones, and a defined jawline",
    ),
    (
        "polish", "Polish actress of Polish heritage",
        "pale neutral skin, deep-set gray eyes, medium straight brows, fine elegant lips",
        "a softly squared face, naturally balanced with a broad straight nose, prominent cheekbones, and a rounded chin",
    ),
    (
        "italian", "Italian actress of Italian heritage",
        "warm olive skin, slightly upturned dark-brown eyes, softly angled brows, a subtly fuller lower lip",
        "a refined diamond-shaped face, naturally balanced with a Roman-profile nose, high wide cheekbones, and a tapered jaw",
    ),
    (
        "greek", "Greek actress of Greek heritage",
        "lightly tanned skin, wide-set amber-brown eyes, defined natural brows, gently bow-shaped lips",
        "a broad oval face, naturally balanced with a gently curved nose, softly rounded cheeks, and a strong rounded chin",
    ),
)
CROSSED_SEEDS = tuple(identity_seed(f"seed-identity-cross-{number}") for number in range(1, 7))
CROSSED_STYLE = {
    "name": "fixed",
    "kind": "environment",
    "pose": "Square to camera with relaxed shoulders and a calm direct gaze",
    "wardrobe": "simple fitted crew-neck knit top in muted navy",
    "background": "soft warm-gray studio background with broad diffused beauty light",
}


def crossed_identity_prompt(identity):
    slug, nationality, appearance, face = identity
    profile = dict(DISTINCT_PROFILES[0], **{
        "slug": slug,
        "identity": nationality,
        "age": 29,
        "appearance": appearance,
        "face": face,
        "hair": "long chestnut-brown waves with a center part",
        "feature": "a subtle beauty mark near her cheek",
        "build": "a fit, athletic build with a medium bust",
        "beauty_direction": "charismatic, distinctive-looking, and naturally photogenic",
        "makeup": "Understated camera-ready makeup with visible natural skin texture, softly defined eyes, and muted lips.",
    })
    return generation_prompt(profile, CROSSED_STYLE, age_wording="band") + (
        " Photorealistic portrait photography with realistic adult facial proportions, "
        "fine natural pores, subtle skin texture, and lifelike hair detail."
    )


SEED_IDENTITY_CROSS_PILOT = tuple(
    (
        f"p{prompt_number:02d}-{identity[0]}-s{seed_number:02d}",
        crossed_identity_prompt(identity),
        seed,
    )
    for prompt_number, identity in enumerate(CROSSED_IDENTITIES, 1)
    for seed_number, seed in enumerate(CROSSED_SEEDS, 1)
)
assert len(SEED_IDENTITY_CROSS_PILOT) == 36
assert Counter(text for _, text, _ in SEED_IDENTITY_CROSS_PILOT) == {
    crossed_identity_prompt(identity): 6 for identity in CROSSED_IDENTITIES
}
assert Counter(seed for _, _, seed in SEED_IDENTITY_CROSS_PILOT) == {
    seed: 6 for seed in CROSSED_SEEDS
}


def configure(workflow, model, steps, cfg, sampler):
    samplers = [node for node in workflow.values() if node.get("class_type") == "KSampler"]
    if len(samplers) != 1:
        raise ValueError(f"expected one KSampler, found {len(samplers)}")
    samplers[0]["inputs"].update({"steps": steps, "sampler_name": sampler, "scheduler": "simple", "denoise": 1, "cfg": cfg})
    loaders = [node for node in workflow.values() if node.get("class_type") == "UnetLoaderGGUF"]
    if len(loaders) != 1:
        raise ValueError(f"expected one UnetLoaderGGUF, found {len(loaders)}")
    loaders[0]["inputs"]["unet_name"] = model


def configure_guidance(workflow, cfg=None, negative=None):
    samplers = [node for node in workflow.values() if "KSampler" in node.get("class_type", "")]
    if cfg is not None:
        linked = {
            str(value[0])
            for node in samplers
            if isinstance((value := node.get("inputs", {}).get("cfg")), list)
        }
        if linked:
            if len(linked) != 1 or next(iter(linked)) not in workflow:
                raise ValueError("samplers have no single resolvable CFG input")
            workflow[next(iter(linked))]["inputs"]["value"] = cfg
        else:
            for node in samplers:
                node["inputs"]["cfg"] = cfg
    if negative is not None:
        node_ids = {
            str(node.get("inputs", {}).get("negative", [None])[0])
            for node in samplers if isinstance(node.get("inputs", {}).get("negative"), list)
        }
        if len(node_ids) != 1 or next(iter(node_ids)) not in workflow:
            raise ValueError("samplers have no single resolvable negative prompt")
        prompt_node = workflow[next(iter(node_ids))]
        prompt_key = next((key for key in ("text", "prompt") if key in prompt_node.get("inputs", {})), None)
        prompt_input = prompt_node["inputs"].get(prompt_key) if prompt_key else None
        if isinstance(prompt_input, list) and prompt_input and str(prompt_input[0]) in workflow:
            source = workflow[str(prompt_input[0])]["inputs"]
            source[next((key for key in ("value", "text", "prompt") if key in source), "value")] = negative
        elif prompt_key:
            prompt_node["inputs"][prompt_key] = negative
        else:
            raise ValueError("negative prompt node has no text or prompt input")


def configure_t_enhancer(workflow, strength):
    bypass = [
        node for node in workflow.values()
        if node.get("class_type") == "LoraLoaderModelOnly"
        and "filterbypass" in node.get("inputs", {}).get("lora_name", "").lower()
    ]
    if len(bypass) == 1:
        model = bypass[0]["inputs"]["model"]
        bypass[0].clear()
        bypass[0].update({
            "inputs": {"enabled": True, "strength": strength, "debug": False, "model": model},
            "class_type": "ComfyUI-Krea2T-Enhancer",
        })
        return
    if bypass:
        raise ValueError(f"expected at most one filter-bypass LoRA, found {len(bypass)}")

    samplers = [node for node in workflow.values() if "KSampler" in node.get("class_type", "")]
    model_links = {
        tuple(node.get("inputs", {}).get("model", ()))
        for node in samplers
        if isinstance(node.get("inputs", {}).get("model"), list)
    }
    if not samplers or len(model_links) != 1:
        raise ValueError("samplers have no single resolvable model input")
    node_id = str(max(map(int, workflow)) + 1)
    workflow[node_id] = {
        "inputs": {
            "enabled": True,
            "strength": strength,
            "debug": False,
            "model": list(next(iter(model_links))),
        },
        "class_type": "ComfyUI-Krea2T-Enhancer",
    }
    for sampler in samplers:
        sampler["inputs"]["model"] = [node_id, 0]


def configure_filter_bypass(workflow, strength):
    samplers = [node for node in workflow.values() if "KSampler" in node.get("class_type", "")]
    model_links = {
        tuple(node.get("inputs", {}).get("model", ()))
        for node in samplers
        if isinstance(node.get("inputs", {}).get("model"), list)
    }
    if not samplers or len(model_links) != 1:
        raise ValueError("samplers have no single resolvable model input")
    node_id = str(max(map(int, workflow)) + 1)
    workflow[node_id] = {
        "inputs": {
            "lora_name": "krea2filterbypass.safetensors",
            "strength_model": strength,
            "model": list(next(iter(model_links))),
        },
        "class_type": "LoraLoaderModelOnly",
    }
    for sampler in samplers:
        sampler["inputs"]["model"] = [node_id, 0]


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="ComfyUI base URL")
    parser.add_argument("--mode", choices=("prompt-control", "stability", "calibration", "identity-diversity", "refinement-pilot", "face-combination-pilot", "seed-identity-cross-pilot", "pilot-v2", "performers", "performers-v4", "performers-v5", "performers-v6", "quality"), default="calibration")
    parser.add_argument("--label", help="server output folder; defaults to <mode>-v1")
    parser.add_argument("--variant", type=int, action="append")
    parser.add_argument("--candidate", type=int, choices=range(1, 5), action="append", help="performer candidate numbers to run")
    parser.add_argument("--start", type=int, default=1, help="first variant when resuming a sequential run")
    parser.add_argument("--stop", type=int, help="last variant to run")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--vary-seed", action="store_true", help="vary identities in prompt experiments")
    parser.add_argument("--steps", type=int, choices=range(1, 101), default=6)
    parser.add_argument("--cfg", type=float, help="override the workflow CFG value")
    parser.add_argument("--negative", help="override the workflow negative prompt")
    parser.add_argument("--t-enhancer-strength", type=float, help="apply the Krea2 T-Enhancer")
    parser.add_argument("--bypass-strength", type=float, help="apply the Krea2 filter-bypass LoRA")
    parser.add_argument("--age-emphasis", action="store_true", help="reinforce the performer's exact age with positive cues")
    parser.add_argument("--age-wording", choices=("exact", "band", "vague"), default="vague")
    parser.add_argument("--background", help="override the performer background and lighting prompt")
    parser.add_argument("--hair", help="override the performer hair prompt")
    parser.add_argument("--suffix", help="append a label to generated filenames")
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--download-dir", type=Path, help="keep downloaded images in this directory")
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.cfg is not None and args.cfg <= 0:
        parser.error("--cfg must be positive")
    if args.t_enhancer_strength is not None and args.t_enhancer_strength <= 0:
        parser.error("--t-enhancer-strength must be positive")
    if args.bypass_strength is not None and args.bypass_strength <= 0:
        parser.error("--bypass-strength must be positive")
    if args.t_enhancer_strength is not None and args.bypass_strength is not None:
        parser.error("--t-enhancer-strength and --bypass-strength are mutually exclusive")
    if args.background and args.mode not in {"performers", "performers-v4", "performers-v5", "performers-v6"}:
        parser.error("--background requires a performer mode")
    if args.hair and args.mode not in {"performers", "performers-v4", "performers-v5", "performers-v6"}:
        parser.error("--hair requires a performer mode")
    if args.age_emphasis and args.age_wording != "exact":
        parser.error("--age-emphasis requires --age-wording exact")
    if args.suffix and not re.fullmatch(r"[A-Za-z0-9_-]+", args.suffix):
        parser.error("--suffix may contain only letters, numbers, underscores, and hyphens")

    entries = {
        "prompt-control": PROMPT_CONTROL,
        "stability": STABILITY,
        "calibration": CALIBRATION,
        "identity-diversity": IDENTITY_DIVERSITY,
        "refinement-pilot": REFINEMENT_PILOT,
        "face-combination-pilot": FACE_COMBINATION_PILOT,
        "seed-identity-cross-pilot": SEED_IDENTITY_CROSS_PILOT,
        "pilot-v2": PILOT,
        "performers": PERFORMERS,
        "performers-v4": EXPANSION,
        "performers-v5": DISTINCT_EXPANSION,
        "performers-v6": PRODUCTION_EXPANSION,
        "quality": QUALITY,
    }[args.mode]
    selected = args.variant or list(range(args.start, (args.stop or len(entries)) + 1))
    if not selected or any(number < 1 or number > len(entries) for number in selected):
        parser.error(f"{args.mode} mode has {len(entries)} variants")
    if args.candidate:
        if args.mode not in {"performers", "performers-v4", "performers-v5", "performers-v6"}:
            parser.error("--candidate requires a performer mode")
        selected = [number for number in selected if entries[number - 1][1] in args.candidate]
        if not selected:
            parser.error("no selected variants match --candidate")
    label = args.label or ("performers-v4b" if args.mode == "performers-v4" else f"{args.mode}-v1")
    workflow_path = args.workflow or root / "workflows" / (
        "krea2-text2img.json" if args.mode == "quality" else "krea2-gguf-turbo-bypass.json"
    )
    workflow_text = workflow_path.read_text()
    default_seed = 2026072600 if args.mode == "performers-v4" else 2026072506 if args.mode == "quality" else 2026072500
    base_seed = args.seed if args.seed is not None else default_seed

    for position, number in enumerate(selected, 1):
        entry = entries[number - 1]
        if args.mode == "quality":
            name = entry["name"]
            text = performer_prompt(PERFORMER_PROFILES[5], entry["clean"])
            model, steps, cfg, sampler = entry["model"], entry["steps"], entry["cfg"], entry["sampler"]
        elif args.mode in {"performers", "performers-v4", "performers-v5", "performers-v6"}:
            profile, candidate, _, style = entry
            name = f"{profile['slug']}-c{candidate:02d}-{style['name']}"
            text = generation_prompt(
                profile, style, args.age_emphasis, args.background, args.age_wording, args.hair
            )
        elif args.mode == "pilot-v2":
            profile, pose, wardrobe, candidate, _ = entry
            treatment = "photography" if candidate == 3 else "glamour"
            name = f"{profile['slug']}-v2-c{candidate:02d}-{treatment}"
            text = pilot_prompt(profile, pose, wardrobe, candidate == 3)
        elif args.mode in {"identity-diversity", "refinement-pilot", "face-combination-pilot", "seed-identity-cross-pilot"}:
            name, text, _ = entry
        else:
            name = entry[0]
            if args.mode in {"prompt-control", "stability"}:
                text = entry[1]
            elif args.mode == "calibration":
                text = calibration_prompt(entry[1])
        if args.suffix:
            name = f"{name}_{args.suffix}"
        if args.mode in {"identity-diversity", "refinement-pilot", "face-combination-pilot", "seed-identity-cross-pilot"}:
            seed = entry[2]
        elif args.mode == "stability":
            seed = base_seed + entry[2]
        elif args.mode == "pilot-v2":
            seed = base_seed + entry[4]
        elif args.mode in {"performers", "performers-v4", "performers-v5", "performers-v6"}:
            seed = base_seed + entry[2]
        else:
            seed = base_seed + number if args.vary_seed else base_seed
        detail = (
            f"{model} — {steps} steps — CFG {cfg:g} — {sampler}/simple"
            if args.mode == "quality" else workflow_path.name
        )
        print(f"[{position}/{len(selected)}] {number:02d} — {name} — seed {seed} — {detail}")
        print(text)
        if args.dry_run:
            continue
        workflow = json.loads(workflow_text)
        if args.mode == "quality":
            configure(workflow, model, steps, cfg, sampler)
        if args.t_enhancer_strength is not None:
            configure_t_enhancer(workflow, args.t_enhancer_strength)
        if args.bypass_strength is not None:
            configure_filter_bypass(workflow, args.bypass_strength)
        configure_guidance(workflow, args.cfg, args.negative)
        prepare(workflow, text, seed, f"cover-story/experiments/{label}/{number:02d}-{name}_")
        if args.download_dir:
            args.download_dir.mkdir(parents=True, exist_ok=True)
            result = run(args.server, workflow, args.download_dir, args.timeout)
        else:
            with tempfile.TemporaryDirectory(prefix="cover-story-experiment-") as directory:
                result = run(args.server, workflow, Path(directory), args.timeout)
        remote = result["images"][0]["remote"]
        print(json.dumps({
            "prompt_id": result["prompt_id"],
            "filename": remote["filename"],
            "subfolder": remote.get("subfolder", ""),
            "type": remote.get("type", "output"),
        }))


if __name__ == "__main__":
    main()
