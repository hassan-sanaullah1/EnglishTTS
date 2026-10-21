"""Build the Urdu-script lexicon from the Mari Energies knowledge base.

Why: in English speech these names come out wrong (ASR heard "Fauji" as "4G"). Writing the name in Urdu
script inside the otherwise-English sentence makes the model use its Urdu phones, which needs no training.
Urdu script is the natural fit because the knowledge base already holds these names in Urdu.

Three categories, as asked for:
  places   gas fields, blocks, processing facilities, provinces and cities
  people   the board, the management team and the group companies
  country  "Pakistan" and "Pakistani"

The Urdu spellings are authored here (orthography is not derivable from the English), but every entry is
CHECKED against the knowledge base, and any KB name that still has no entry is reported - so the lexicon
cannot silently fall behind the KB.

Usage:  python app/build_lexicon.py [--kb /path/to/mari_energies_knowledge_base.md]
Output: app/lexicon.json   {"roman": "urdu", ...}
"""
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KB = os.path.expanduser("~/Desktop/kaani/mari_energies_knowledge_base.md")
OUT = os.path.join(HERE, "lexicon.json")

# The country first: the user asked for these two specifically.
COUNTRY = {
    "Pakistani": "پاکستانی",
    "Pakistan": "پاکستان",
}

# Gas fields, blocks, facilities, provinces, cities.
PLACES = {
    "Mari": "ماڑی",
    "Sujawal": "سجاول",
    "Sachal": "سچل",
    "Daharki": "ڈہرکی",
    "Waziristan": "وزیرستان",
    "Shewa": "شیوا",
    "Spinwam": "سپین وام",
    "Bhitai": "بھٹائی",
    "Ghazij": "غازج",
    "Zarghun": "زرغون",
    "Hilal": "ہلال",
    "Karakoram": "قراقرم",
    "Hangu": "ہنگو",
    "Ghotki": "گھوٹکی",
    "Pateji": "پاتیجی",
    "Goru": "گورو",
    "Halini": "ہالینی",
    "Kawagarh": "کاواگڑھ",
    "Samanasuk": "سمن سک",
    "Shawal": "شوال",
    "Bannu": "بنوں",
    "Attock": "اٹک",
    "Quetta": "کوئٹہ",
    "Balochistan": "بلوچستان",
    "Sindh": "سندھ",
    "Islamabad": "اسلام آباد",
    "Karachi": "کراچی",
    "Lahore": "لاہور",
    "Pakhtunkhwa": "پختونخوا",
    "Khyber": "خیبر",
    "Reko Diq": "ریکوڈک",
    "Abu Dhabi": "ابوظہبی",
    "Mughal": "مغل",
    "Kissan": "کسان",
    "Dost": "دوست",
    "Roshan": "روشن",
    "Mustaqbil": "مستقبل",
    "Sehar": "سحر",
    "Noor": "نور",
    "Makran": "مکران",
    "Kabirwala": "کبیروالا",
    "Okara": "اوکاڑہ",
    "Kot": "کوٹ",
    "Khel": "خیل",
    "Jhim": "جھم",
    "Dughan": "دوغان",
    "Bijjar": "بجار",
    "Maiwand": "میوند",
    "Gharonda": "گھروندا",
    "Rani": "رانی",
    "Chowk": "چوک",
    "Azadi": "آزادی",
    "Dastarkhwan": "دسترخوان",
    "Dastarkhawn": "دسترخوان",
    "Aligarh": "علی گڑھ",
    "Pab": "پاب",
    "Naya": "نیا",
    "Lockhart": "لاک ہارٹ",
}

# People and the group companies named after people/places. Multi-word first - the matcher is
# longest-match, so "Anwar Ali Hyder" wins over "Hyder".
PEOPLE = {
    "Anwar Ali Hyder": "انور علی حیدر",
    "Syed Bakhtiyar Kazmi": "سید بختیار کاظمی",
    "Faheem Haider": "فہیم حیدر",
    "Syed Shahzad Nabi": "سید شہزاد نبی",
    "Hamed Yaqoob Sheikh": "حامد یعقوب شیخ",
    "Zafar Abbas": "ظفر عباس",
    "Ahmed Hayat Lak": "احمد حیات لک",
    "Muhammad Aamir Salim": "محمد عامر سلیم",
    "Abid Niaz Hasan": "عابد نیاز حسن",
    "Seema Adil": "سیما عادل",
    "Ayla Majid": "عائلہ ماجد",
    "Sumair Ashraf Sheikh": "سمیر اشرف شیخ",
    "Nabeel Rasheed": "نبیل رشید",
    "Ishfaq Nadeem Ahmad": "اشفاق ندیم احمد",
    "Raza Muhammad Khan": "رضا محمد خان",
    "Mushtaq Hussain": "مشتاق حسین",
    "Imtiaz Shaheen": "امتیاز شاہین",
    "Muhammad Afzal Janjua": "محمد افضل جنجوعہ",
    "Khalid Nawaz Malik": "خالد نواز ملک",
    "Ghulam Muhammad Malik": "غلام محمد ملک",
    "Mehmood Aslam Hayat": "محمود اسلم حیات",
    "Hamid Niaz": "حامد نیاز",
    # single names, as fall-backs when a surname appears alone
    "Fauji": "فوجی",
    "Hyder": "حیدر",
    "Haider": "حیدر",
    "Faheem": "فہیم",
    "Bakhtiyar": "بختیار",
    "Kazmi": "کاظمی",
    "Shahzad": "شہزاد",
    "Yaqoob": "یعقوب",
    "Aamir": "عامر",
    "Niaz": "نیاز",
    "Ayla": "عائلہ",
    "Majid": "ماجد",
    "Seema": "سیما",
    "Zafar": "ظفر",
    "Abbas": "عباس",
    "Syed": "سید",
    "Sheikh": "شیخ",
    "Ghani": "غنی",
    "Tuzgi": "تزگی",
    "Ammuri": "عموری",
    "Askari": "عسکری",
    "Meezan": "میزان",
    "Faysal": "فیصل",
    "Habib": "حبیب",
    "Alfalah": "الفلاح",
    "Kehkashan": "کہکشاں",
    "Panni": "پنی",
    "Asif": "آصف",
    "Bakhsh": "بخش",
    "Daud": "داؤد",
    "Laghari": "لغاری",
    "Alhaj": "الحاج",
    "Abdullah": "عبداللہ",
    "Hazoor": "حضور",
}

# English words that are correctly spelled but still come out wrong, fixed with a plain Roman
# respelling rather than Urdu script - these are ordinary English words, not Pakistani names, so
# there's no reason to switch script mid-sentence (untested risk of the accent/voice drifting
# on the switch) when nudging the one ambiguous letter in Latin spelling is enough.
PRONUNCIATION = {
    # "g" before "e/i/y" is usually soft in English (gem, gym, energy) but OmniVoice read it
    # hard, like the g in "gear" - heard directly in the "Mari Energies" sentences on both
    # voices. "Enerjies" forces the /dʒ/ ("j") reading without changing anything else about it.
    "Energies": "Enerjies",
    "energies": "enerjies",
}

# Names in the KB that are deliberately NOT swapped, with the reason.
SKIP = {
    "Sky47": "a brand read as English letters plus a number",
    "Huawei": "a Chinese brand, not an Urdu word",
    "Technologies": "an ordinary English word",
    "Minerals": "an ordinary English word",
    "Engro": "a company brand, read as English",
    "Kubernetes": "a software product name",
    "Planetive": "a company brand",
    "Corplink": "a company brand",
    "Petroserv": "a company brand",
    "Analytics": "an ordinary English word",
    "Condensate": "an ordinary English word",
    "Decarbonization": "an ordinary English word",
    "Digitalization": "an ordinary English word",
    "Geoscientists": "an ordinary English word",
    "Commerciality": "an ordinary English word",
}


def build():
    lex = {}
    lex.update(PEOPLE)      # people first; the sort below puts long phrases ahead anyway
    lex.update(PLACES)
    lex.update(COUNTRY)
    lex.update(PRONUNCIATION)
    return lex


def check_against_kb(lex, kb_path):
    """Report entries the KB does not contain, and KB names that have no entry yet."""
    if not os.path.exists(kb_path):
        return [f"knowledge base not found at {kb_path}; lexicon written unchecked"], []
    kb = open(kb_path, encoding="utf-8").read()
    # PRONUNCIATION entries fix English orthography, not KB names - a lowercase variant like
    # "energies" need not appear in the KB literally, so don't flag it as a likely typo.
    not_in_kb = [r for r in lex if r not in kb and r not in PRONUNCIATION]

    # KB words that look like names we have not covered: capitalised, not ordinary English
    body = re.sub(r"`[^`]*`|\[[^\]]*\]\([^)]*\)", " ", kb)
    caps = set(re.findall(r"\b[A-Z][a-z]{2,}\b", body))
    english = set()
    for p in ("/usr/share/dict/words", "/usr/share/dict/american-english"):
        if os.path.exists(p):
            english = {w.strip().lower() for w in open(p, encoding="utf-8", errors="ignore")}
            break
    covered = set()
    for r in lex:
        covered.update(r.split())
    uncovered = sorted(w for w in caps
                       if w.lower() not in english and w not in covered and w not in SKIP)
    return not_in_kb, uncovered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.environ.get("MARI_KB", DEFAULT_KB))
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    lex = build()
    not_in_kb, uncovered = check_against_kb(lex, a.kb)

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(lex, f, indent=2, ensure_ascii=False, sort_keys=True)

    print(f"{len(lex)} entries -> {a.out}")
    print(f"  people {len(PEOPLE)}   places {len(PLACES)}   country {len(COUNTRY)}   "
          f"pronunciation {len(PRONUNCIATION)}")
    if not_in_kb:
        print(f"\nin the lexicon but NOT found in the knowledge base ({len(not_in_kb)}) - "
              f"harmless, but check for typos:")
        print("  " + ", ".join(sorted(not_in_kb)))
    if uncovered:
        print(f"\nnames in the knowledge base with no Urdu spelling yet ({len(uncovered)}) - "
              f"add them to PLACES/PEOPLE if the kiosk ever says them:")
        for i in range(0, min(len(uncovered), 60), 10):
            print("  " + ", ".join(uncovered[i:i + 10]))


if __name__ == "__main__":
    main()
