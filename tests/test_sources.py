from hn_radio import sources


def test_parse_github_extracts_owner_repo():
    assert sources.parse_github("https://github.com/leonickson1/Swiftlet") == ("leonickson1", "Swiftlet")
    assert sources.parse_github("https://github.com/foo/bar.git") == ("foo", "bar")
    assert sources.parse_github("https://github.com/foo/bar#readme") == ("foo", "bar")


def test_parse_github_rejects_non_repo():
    assert sources.parse_github("https://example.com/foo/bar") is None
    assert sources.parse_github("https://github.com/features") is None
    assert sources.parse_github(None) is None


def test_extract_readable_strips_tags_scripts_and_entities():
    html_text = (
        "<html><head><style>.x{color:red}</style><script>evil()</script></head>"
        "<body><h1>Title</h1><p>Hello &amp; welcome to the **repo**.</p></body></html>"
    )
    out = sources.extract_readable(html_text)
    assert "evil()" not in out and "color:red" not in out
    assert "<" not in out and ">" not in out
    assert "Hello & welcome to the  repo" in out.replace("  ", " ").replace("  ", " ") or "welcome to the" in out


def test_summarize_extractive_takes_first_substantial_sentences():
    text = (
        "Nav\nHome\n"
        "This project streams model weights from disk to run large models on small devices. "
        "It repacks experts for a single read and caches the hot ones. "
        "Benchmarks are honest about the tradeoffs.\n"
        "footer link"
    )
    out = sources.summarize_extractive(text, max_sentences=2)
    assert "streams model weights" in out
    assert out.count(".") <= 2 + 1
    assert "Nav" not in out and "footer" not in out


def test_extract_paragraphs_keeps_body_drops_chrome():
    html_text = (
        "<nav>Home About Blog</nav><h1>My Post Title</h1>"
        "<p>This is a real paragraph with plenty of words to count as body text.</p>"
        "<p>short</p><footer>links and copyright</footer>"
    )
    out = sources.extract_paragraphs(html_text)
    assert "real paragraph with plenty of words" in out
    assert "Home About" not in out and "copyright" not in out
    assert "short" not in out  # below the word threshold


def test_extract_paragraphs_empty_when_no_paragraphs():
    assert sources.extract_paragraphs("# A README\n\nsome markdown, no p tags") == ""


def test_summarize_empty_is_safe():
    assert sources.summarize_extractive("") == ""
    assert sources.summarize_extractive("one two three") == ""  # too short, no 6+ word sentence
