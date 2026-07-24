from app.binance import BinanceClient
from app.config import Settings


def test_prefers_one_quote_pair_per_base_asset(monkeypatch,tmp_path):
    client=BinanceClient(Settings(temp_data_dir=tmp_path,quote_assets=('USDT','USDC','FDUSD')))
    payload={'symbols':[
        {'symbol':'AAAUSDC','baseAsset':'AAA','quoteAsset':'USDC','status':'TRADING','isSpotTradingAllowed':True,'permissions':['SPOT']},
        {'symbol':'AAAUSDT','baseAsset':'AAA','quoteAsset':'USDT','status':'TRADING','isSpotTradingAllowed':True,'permissions':['SPOT']},
        {'symbol':'BBBUSDC','baseAsset':'BBB','quoteAsset':'USDC','status':'TRADING','isSpotTradingAllowed':True,'permissions':['SPOT']},
    ]}
    monkeypatch.setattr(client,'_get_json',lambda *args,**kwargs: payload)
    rows=client.active_spot_symbols(('USDT','USDC','FDUSD'))
    assert {x['symbol'] for x in rows}=={'AAAUSDT','BBBUSDC'}
