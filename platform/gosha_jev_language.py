"""English-only JEV input and conservative checks against the source utterance.

The translation is advisory. A translation that loses a Russian cancellation
must never turn into a physical command. The checks below intentionally reject
some valid requests; the caller can ask for clarification or use the old route.
"""
import re


class LanguageError(ValueError):
    pass


_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_NEGATION = re.compile(
    r"(?iu)\b(?:не|нет|нельзя|никогда|никак|не надо|не нужно|отмена|отмен[а-я]*|передумал[а-я]*|не сейчас|no|not|don't|do not|never|cancel)\b"
)
_LEFT = re.compile(r"(?iu)\b(?:лев[а-я]*|left)\b")
_COUNT = re.compile(r"(?iu)\b(?:\d+|один|одну|два|две|три|четыре|пять|one|two|three|four|five|twice)\b")
_DISCUSSION = re.compile(r"(?iu)\b(?:цитат[а-я]*|фраз[а-я]*|перевед[а-я]*|перевести|означа[а-я]*|сказал[а-я]*|говорил[а-я]*|quote|quoted|translate|means|said)\b")
_QUESTION = re.compile(r"(?iu)\b(?:можешь|умеешь|сможешь|сколько|почему|зачем|can you|could you|how many)\b")
_MOTION_EXPLANATION = re.compile(r"(?iu)\b(?:как|что|если|когда|где|могу|можно|where|when|what|why|how|may i)\b")
_MOTION_HINTS = {
    "builtin/hand_wave": re.compile(r"(?iu)\b(?:помаш[а-я]*|маш[а-я]*|помах[а-я]*|мах[а-я]*|взмах[а-я]*|wave)\b"),
    "builtin/walk_forward": re.compile(r"(?iu)\b(?:впер[её]д|forward)\b"),
    "builtin/walk_backward": re.compile(r"(?iu)\b(?:назад|backward)\b"),
}
_STOP_HINT = re.compile(r"(?iu)\b(?:стоп|стой|останов[а-я]*|прекрат[а-я]*|stop)\b")
_OPPOSITE = {
    "builtin/walk_forward": _MOTION_HINTS["builtin/walk_backward"],
    "builtin/walk_backward": _MOTION_HINTS["builtin/walk_forward"],
}


def is_priority_stop(source_text):
    """A direct stop can bypass JEV, but a quote or question cannot."""
    return bool(isinstance(source_text, str) and _STOP_HINT.search(source_text)
                and not _NEGATION.search(source_text)
                and not _DISCUSSION.search(source_text)
                and not _QUESTION.search(source_text)
                and not any(hint.search(source_text) for hint in _MOTION_HINTS.values())
                and "?" not in source_text)


def validate_english(text):
    """Fail closed before calling JEV if text is not a short English utterance."""
    if (not isinstance(text, str) or not text.strip() or len(text) > 2000
            or _CYRILLIC.search(text) or not text.isascii()
            or any(ord(ch) < 32 and ch not in "\t\n" for ch in text)):
        raise LanguageError("english_translation_required")
    return text.strip()


def guard_physical_choice(source_text, english_text, choice):
    """Return a safe route; never trust a translated motion by itself."""
    validate_english(english_text)
    if not isinstance(source_text, str) or not source_text.strip() or len(source_text) > 2000:
        return "clarify"
    source = source_text.strip()
    if _NEGATION.search(source):
        return "no_action"
    if choice == "stop" and not _STOP_HINT.search(source):
        return "clarify"
    if choice in _MOTION_HINTS:
        if (_LEFT.search(source) or _COUNT.search(source) or _DISCUSSION.search(source)
                or _QUESTION.search(source) or _MOTION_EXPLANATION.search(source)
                or "?" in source or not _MOTION_HINTS[choice].search(source)
                or (choice in _OPPOSITE and _OPPOSITE[choice].search(source))):
            return "clarify"
    return choice


class LocalEnglishTranslator:
    """A warm, offline Russian→English translator; load only in JEV mode."""

    def __init__(self, model_dir, *, threads=4):
        from pathlib import Path
        import ctranslate2
        import sentencepiece

        root = Path(model_dir)
        if not (root / "model/model.bin").is_file() or not (root / "sentencepiece.model").is_file():
            raise LanguageError("translation_model_missing")
        self.encoder = sentencepiece.SentencePieceProcessor(model_file=str(root / "sentencepiece.model"))
        self.model = ctranslate2.Translator(str(root / "model"), device="cpu",
                                            inter_threads=1, intra_threads=threads)

    def translate(self, source_text):
        if not isinstance(source_text, str) or not source_text.strip() or len(source_text) > 2000:
            raise LanguageError("invalid_source_text")
        if not _CYRILLIC.search(source_text):
            return validate_english(source_text)
        tokens = self.encoder.encode(source_text, out_type=str)
        outputs = self.model.translate_batch([tokens], beam_size=4)
        return validate_english(self.encoder.decode(outputs[0].hypotheses[0]))
