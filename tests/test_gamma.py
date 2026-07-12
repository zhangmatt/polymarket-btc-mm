from polymarket_mm.gamma import extract_condition_id, extract_token_ids, parse_market_tokens


def test_extracts_tokens_from_gamma_json_strings():
    market = {
        "conditionId": "0xabc",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }
    assert extract_condition_id(market) == "0xabc"
    assert extract_token_ids(market) == ("111", "222")


def test_parse_market_tokens_derives_window_from_slug():
    tokens = parse_market_tokens(
        "btc-updown-15m-1768429800",
        {
            "conditionId": "0xabc",
            "tokens": [
                {"outcome": "Up", "tokenId": "111"},
                {"outcome": "Down", "tokenId": "222"},
            ],
        },
    )
    assert tokens.start_ts == 1768429800
    assert tokens.end_ts == 1768430700
    assert tokens.up_token == "111"
    assert tokens.down_token == "222"
