from typing import Optional

from data.defillama import get_protocol_tvl_data

def get_protocol_data(name: str):
    return get_protocol_tvl_data(name)
