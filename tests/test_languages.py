from backend import languages


def test_language_is_detected_from_the_script():
    assert languages.detect("ನಮಸ್ಕಾರ, ನನ್ನ ಬೇಕರಿಗೆ ಒಂದು ವೆಬ್‌ಸೈಟ್ ಮಾಡಿ") == "kn"
    assert languages.detect("मेरी बेकरी के लिए एक वेबसाइट बनाइए") == "hi"
    assert languages.detect("Build an Avengers Doomsday website") == "en"
    # Kannada with an English word or two is still Kannada
    assert languages.detect("ಹೆಡರ್ dark green ಮಾಡು, ಬ್ಯಾಕ್‌ಗ್ರೌಂಡ್ ಬದಲಿಸು") == "kn"
    assert languages.detect("") == "en"


def test_reply_rule_only_for_other_languages():
    assert languages.reply_rule("en") == ""
    assert "Reply ONLY in Kannada" in languages.reply_rule("kn")
