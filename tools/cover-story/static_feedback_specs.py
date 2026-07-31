"""Curated v3 feedback actions for static Cover Story performer replacements."""

KEEP_CHANGES = {247, 266, 283, 338, 405, 458}

FACE_REJECTS = {
    17, 24, 25, 27, 32, 41, 51, 52, 53, 56, 107, 114, 210, 216, 219,
    221, 227, 235, 249, 257, 268, 275, 288, 291, 353, 360, 368, 378,
    383, 392, 404, 411, 412, 415, 417, 420, 423, 430, 433, 444, 452,
    463, 464, 475, 476, 479, 488,
}
FACE_MAYBES = {54, 63, 196, 220, 459, 471}
TOO_OLD = {25, 53, 56, 221, 235, 249, 275, 291, 378, 392, 404, 420}
UNKNOWN_REJECTS = {175}

WARDROBE_OVERRIDES = {
    4: "fitted deep-petrol satin top with a sculpted sweetheart neckline",
    6: "fitted midnight-blue ribbed-knit dress bodice with a structured square neckline",
    13: "dusty-rose bandeau top under a camel lightweight cropped cardigan",
    15: "close-fitting champagne satin one-shoulder bodysuit with sculpted ruching",
    18: "fitted ivory silk wrap top with smooth corset-inspired seams",
    21: "close-fitting ivory silk blouse with an open collar and fitted torso",
    22: "fitted deep-teal silk shell with a structured square neckline",
    31: "fitted cool-white jacquard top with a subtle black botanical pattern and portrait neckline",
    35: "close-fitting black satin corset camisole with silver chain straps",
    36: "smooth fitted ivory silk top with a clean square neckline",
    42: "fitted crimson satin bodysuit with an open collar",
    44: "fitted petrol-blue satin cowl-neck bodysuit",
    46: "fitted crimson bodysuit with a small keyhole neckline",
    47: "close-fitting cool-white silk halter bodysuit",
    48: "cropped black leather jacket over a fitted ivory ribbed tank",
    51: "fitted ivory silk bodysuit with translucent organza shoulders",
    68: "close-fitting deep-teal satin dress bodice with a wide portrait neckline",
    74: "fitted black jersey top with a sculpted cowl neckline",
    75: "cropped black leather jacket over a fitted cherry-red ribbed tank",
    76: "fitted crimson fine-knit top with subtle ruching",
    77: "fitted charcoal scoop-neck knit top",
    78: "fitted black satin bodysuit with a sculptural folded neckline",
    79: "cropped black leather jacket over a fitted petrol-blue ribbed tank",
    81: "silver chainmail-inspired halter layered over an opaque fitted black bandeau top",
    91: "fitted petrol-blue burnout-velvet top with an asymmetric neckline",
    92: "silver chainmail-inspired halter layered over an opaque fitted black bandeau top",
    104: "close-fitting deep-teal off-the-shoulder jersey top",
    111: "fitted black satin sweetheart-neck bodysuit",
    118: "fitted deep-teal ribbed bodysuit with a folded portrait neckline",
    122: "close-fitting cherry-red satin bodysuit with a wide boat neckline",
    140: "close-fitting midnight-blue cotton dress bodice with a low square neckline",
    144: "close-fitting deep-teal cashmere top with a wide boat neckline",
    165: "petrol-blue bandeau top under a cropped charcoal cardigan worn open",
    175: "fitted deep-teal square-neck bodysuit under a cropped black cardigan worn open",
    178: "fitted gunmetal one-shoulder fine-knit top",
    182: "fitted oxblood satin bodysuit with a high black-lace yoke",
    184: "fitted black lace bodysuit with sheer gathered shoulders",
    185: "fitted black chiffon bodysuit with a high lace collar",
    193: "crimson bandeau top under a cropped black cardigan worn open",
    200: "fitted black ribbed tank with a clean square neckline",
    213: "fitted black satin bodysuit with a sculptural folded neckline",
    218: "structured fitted dark-plum satin corset bodice with a folded neckline",
    219: "cropped black moto jacket over a fitted petrol-blue square-neck bodysuit",
    223: "black leather biker jacket over a fitted ivory satin camisole",
    225: "cropped black leather vest over a fitted crimson band tee",
    226: "smooth fitted oxblood knit top with a wide neckline",
    239: "fitted black blazer worn open over a close-fitting deep-emerald corset-seamed top",
    245: "fitted ivory silk bodysuit with an open collar",
    247: "fitted deep-teal ribbed bodysuit with a portrait neckline",
    254: "dusty-rose bandeau top under a camel lightweight cropped cardigan",
    266: "petrol-blue bandeau top under a cropped charcoal cardigan worn open",
    270: "gunmetal fitted scoop-neck top",
    272: "silver chainmail-inspired halter layered over an opaque fitted black bandeau top",
    283: "tailored charcoal blazer worn open over a fitted ivory satin bodysuit",
    284: "silver chainmail-inspired halter layered over an opaque fitted black bandeau top",
    285: "crimson bandeau top under a cropped black cardigan worn open",
    286: "tailored black vest over a fitted ivory satin bodysuit",
    290: "fitted oxblood satin bodysuit with a high black-lace yoke",
    323: "tailored charcoal blazer worn open over a fitted champagne satin camisole",
    324: "fitted ivory Victorian lace blouse with an open portrait neckline",
    338: "body-hugging deep-emerald satin bodysuit with a wrap-front neckline",
    344: "cropped black cardigan worn open over a fitted crimson camisole with layered chains",
    381: "tailored camel vest over a fitted black satin camisole",
    383: "silver chainmail-inspired halter layered over an opaque fitted black bandeau top",
    384: "fitted ivory square-neck bodysuit under a cropped charcoal jacket",
    385: "fitted midnight-blue tuxedo jacket worn open over a champagne satin cowl-neck bodysuit",
    388: "cropped charcoal cardigan worn open over a fitted champagne satin camisole",
    405: "body-hugging dark-petrol satin evening bodice with an asymmetric neckline and corset seams",
    413: "black zip-front jacket worn open over a fitted ivory square-neck bodysuit",
    414: "fitted soft-gray cashmere bodysuit with a sweetheart neckline",
    417: "fitted deep-teal ribbed bodysuit with a folded portrait neckline",
    431: "crimson bandeau top under a cropped black cardigan worn open",
    434: "fitted crimson satin top with an asymmetric neckline",
    447: "dusty-rose bandeau top under a camel lightweight cropped cardigan",
    458: "body-hugging deep-emerald satin bodysuit with sculptural shoulders",
    459: "fitted black tuxedo vest over a close-fitting champagne sweetheart-neck bodysuit",
    472: "silver chainmail-inspired halter layered over an opaque fitted black bandeau top",
    474: "cropped black structured jacket over a fitted electric-violet bodysuit",
    482: "petrol-blue bandeau top under a cropped charcoal cardigan worn open",
    485: "fitted deep-teal satin blouse with dramatic gathered shoulders",
    499: "smooth fitted black knit sweater with a wide neckline",
}

HAIR_OVERRIDES = {
    32: "a glossy black shoulder-length layered bob with soft side-swept bangs",
    68: "long glossy vivid-pink waves with no other hair color",
    104: "long glossy cherry-red waves with no other hair color",
    142: "long softly waved dark hair with minimal curl",
    176: "a sleek shoulder-length layered lob with a deep side part",
    252: "long straight glossy dark hair with soft face-framing layers",
    409: "a polished chin-length layered bob with side-swept bangs",
}

MAKEUP_OVERRIDES = {
    40: "Fresh natural makeup with softly defined eyes, clear natural skin, and lightly tinted lips.",
    81: "Polished makeup with softly smoky charcoal eye definition, natural skin texture, and satin lips.",
    189: "Polished makeup with softly smoky eye definition, natural skin texture, and satin lips.",
    191: "Polished makeup with softly smoky eye definition, natural skin texture, and satin lips.",
    196: "Fresh glamorous makeup with softly smoky eyes, natural skin texture, and satin lips.",
    369: "Fresh natural makeup with softly defined eyes, clear natural skin, and lightly tinted lips.",
}

POSE_OVERRIDES = {
    84: "A gentle three-quarter angle with relaxed shoulders, direct eye contact, and a warm spontaneous smile",
}

COMPOSITION_OVERRIDES = {
    380: "Waist-up composition with balanced headroom and no legs visible",
}

