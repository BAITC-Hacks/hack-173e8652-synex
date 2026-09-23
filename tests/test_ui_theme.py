from moneygraph.ui.theme import brand_markup


def test_brand_markup_escapes_copy_and_keeps_an_accessible_name() -> None:
    markup = brand_markup(
        product_name="MoneyGraph <AML>",
        descriptor='Trace "money" <script>alert(1)</script>',
    )

    assert "<script>" not in markup
    assert "MoneyGraph &lt;AML&gt;" in markup
    assert "Trace &quot;money&quot; &lt;script&gt;alert(1)&lt;/script&gt;" in markup
    assert 'role="img"' in markup
    assert (
        'aria-label="MoneyGraph &lt;AML&gt; — '
        'Trace &quot;money&quot; &lt;script&gt;alert(1)&lt;/script&gt;"'
    ) in markup
