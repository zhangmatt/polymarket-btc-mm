from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from .config import Credentials
from .positions import CTF_CONTRACT_ADDRESS, POLYGON_USDC_ADDRESS

try:
    from web3 import Web3  # type: ignore
except Exception:  # pragma: no cover - live dependency
    Web3 = None  # type: ignore

try:
    from web3.constants import HASH_ZERO  # type: ignore
except Exception:  # pragma: no cover - live dependency
    HASH_ZERO = b"\x00" * 32  # type: ignore

try:
    from py_builder_relayer_client.client import RelayClient  # type: ignore
    from py_builder_relayer_client.models import OperationType, SafeTransaction  # type: ignore
    from py_builder_signing_sdk.config import BuilderConfig  # type: ignore
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds  # type: ignore
except Exception:  # pragma: no cover - optional live dependency
    RelayClient = None  # type: ignore
    OperationType = None  # type: ignore
    SafeTransaction = None  # type: ignore
    BuilderConfig = None  # type: ignore
    BuilderApiKeyCreds = None  # type: ignore


CTF_MERGE_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "mergePositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


@dataclass(frozen=True)
class MergeConfig:
    rpc_url: str = "https://polygon-rpc.com"
    relayer_url: str = "https://relayer-v2.polymarket.com"
    chain_id: int = 137


def connect_polygon_web3(rpc_url: str = "https://polygon-rpc.com") -> Any:
    if Web3 is None:
        raise RuntimeError("web3 is required for onchain balance and merge operations")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    _inject_poa_middleware(w3)
    return w3


def _inject_poa_middleware(w3: Any) -> None:
    for import_path in (
        ("web3.middleware", "geth_poa_middleware"),
        ("web3.middleware.geth_poa", "geth_poa_middleware"),
        ("web3.middleware", "ExtraDataToPOAMiddleware"),
    ):
        try:
            module = __import__(import_path[0], fromlist=[import_path[1]])
            middleware = getattr(module, import_path[1])
            w3.middleware_onion.inject(middleware, layer=0)
            return
        except Exception:
            continue


def build_relayer_client(credentials: Credentials, config: MergeConfig = MergeConfig()) -> Optional[Any]:
    if RelayClient is None or BuilderConfig is None or BuilderApiKeyCreds is None:
        return None
    if not credentials.has_relayer_credentials():
        return None
    creds = BuilderApiKeyCreds(
        credentials.relayer_api_key,
        credentials.relayer_api_secret,
        credentials.relayer_passphrase,
    )
    builder_config = BuilderConfig(local_builder_creds=creds)
    return RelayClient(
        relayer_url=config.relayer_url,
        chain_id=config.chain_id,
        private_key=credentials.private_key,
        builder_config=builder_config,
    )


class CompleteSetMerger:
    def __init__(
        self,
        *,
        relayer: Any,
        w3: Any,
        proxy_wallet: str,
        ctf_address: str = CTF_CONTRACT_ADDRESS,
        collateral_address: str = POLYGON_USDC_ADDRESS,
    ):
        if Web3 is None:
            raise RuntimeError("web3 is required for complete-set merges")
        self.relayer = relayer
        self.w3 = w3
        self.proxy_wallet = proxy_wallet
        self.ctf_address = ctf_address
        self.collateral_address = collateral_address
        self.ctf = w3.eth.contract(address=Web3.to_checksum_address(ctf_address), abi=CTF_MERGE_ABI)

    def merge(self, *, condition_id: str, shares: float) -> bool:
        if self.relayer is None or shares <= 0:
            return False
        amount = int(math.floor(shares * 1_000_000))
        if amount <= 0:
            return False
        call_data = self.ctf.functions.mergePositions(
            self.collateral_address,
            HASH_ZERO,
            Web3.to_bytes(hexstr=condition_id),
            [1, 2],
            amount,
        ).build_transaction({"from": Web3.to_checksum_address(self.proxy_wallet)})["data"]
        txn = SafeTransaction(
            to=self.ctf_address,
            operation=OperationType.Call,
            data=call_data,
            value="0",
        )
        response = self.relayer.execute([txn], f"merge {shares:.2f} complete sets")
        if hasattr(response, "wait"):
            response.wait()
        return True
