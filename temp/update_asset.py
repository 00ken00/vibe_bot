import datetime as dt
import pandas as pd

from utility.httpx_utils import set_test_client, get_client
from crypto_bot.utils.exchange_auths import coincheck_api_0
from crypto_bot.api import bitflyer, gmo, bitbank
from crypto_bot.utils.tools import solve_collateral_equation

set_test_client()


async def copy_asset():
    btc_price = (await bitbank.Ticker.get("btc_jpy")).mid
    eth_price = (await bitbank.Ticker.get("eth_jpy")).mid
    xrp_price = (await bitbank.Ticker.get("xrp_jpy")).mid
    sol_price = (await bitbank.Ticker.get("sol_jpy")).mid

    gmo_assets = await gmo.Assets.get()
    gmo_total_asset = 0
    for asset in gmo_assets.data:
        if asset.size == 0:
            continue
        if asset.symbol == "JPY":
            gmo_total_asset += asset.size
        else:
            gmo_total_asset += asset.conversionRate * asset.size

    bb_assets = await bitbank.Assets.get()
    bb_positions = await bitbank.Positions.get()
    bb_asset = bb_assets.jpy.onhand_amount
    bb_asset += bb_assets.filter_symbol("btc_jpy").onhand_amount * btc_price
    bb_asset += bb_assets.filter_symbol("eth_jpy").onhand_amount * eth_price
    bb_asset += sum(_.pnl(btc_price) for _ in bb_positions.filter_pair("btc_jpy"))
    bb_asset += sum(_.pnl(eth_price) for _ in bb_positions.filter_pair("eth_jpy"))

    _coincheck_assets = await coincheck_api_0(
        "GET", "/api/accounts/balance", {}, get_client()
    )
    coincheck_total_asset = float(_coincheck_assets["jpy"])
    coincheck_total_asset += float(_coincheck_assets["btc"]) * float(btc_price)

    bitflyer_cfd_asset = (await bitflyer.Collateral.get()).asset

    df = pd.DataFrame(
        [
            {
                "datetime": str(dt.datetime.now())[:19],
                "btc_rate": f"{btc_price:,.0f}",
                "eth_rate": f"{eth_price:,.0f}",
                "xrp_rate": f"{xrp_price:,.0f}",
                "sol_rate": f"{sol_price:,.0f}",
                "gmo": f"{gmo_total_asset:,.0f}",
                "bitbank": f"{bb_asset:,.0f}",
                "coincheck": f"{coincheck_total_asset:,.0f}",
                "bitflyer_cfd": f"{bitflyer_cfd_asset:,.0f}",
            }
        ]
    )

    print(df)
    df.to_clipboard(index=False, header=False)


async def __scratch__():
    await copy_asset()
