from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # API -------------------------------------------------------------------
    api_env: str = "development"
    api_host: str = "127.0.0.1"  # Default to localhost for security
    api_port: int = 8000
    # If None, auth is disabled in dev mode (fail-closed otherwise)
    api_key: SecretStr | None = Field(default=None, repr=False)

    # Persistence ---------------------------------------------------------------
    db_path: str = "data/trading_intelligence.db"
    bus_maxsize: int = 1024

    # CCXT unified venue adapter (ADR 0012) -------------------------------------
    # Master switch for the self-feeding market-data loop: when True (and
    # ccxt_sandbox is True) the composition root starts the CCXT observation
    # adapter and drives observations through ingest -> decision automatically.
    # Default OFF so backtests and the test suite never depend on a venue.
    ccxt_enabled: bool = False
    ccxt_venue_id: str = "binance"
    ccxt_api_key: SecretStr | None = Field(default=None, repr=False)
    ccxt_secret: SecretStr | None = Field(default=None, repr=False)
    ccxt_sandbox: bool = True
    # Live execution is never a default (P0-014). The CCXT order gateway only
    # connects to a production venue when this is explicitly True AND real
    # credentials are set; sandbox mode needs neither.
    ccxt_live_authorized: bool = False
    ccxt_default_symbol: str = "BTC/USDT"
    ccxt_enable_websocket: bool = False
    ccxt_market_type: str = "spot"  # spot | swap | future | delivery

    # Risk-gate feeds (gap G3) ---------------------------------------------------
    # The VPIN toxicity and square-root impact layers only become live when
    # fed. kelly_from_memory is the *learning* feed: per Constitution §5 it may
    # never alter risk parameters without operator approval, so it defaults OFF.
    risk_kelly_from_memory: bool = False
    # Optional operator-supplied venue stats for the impact veto, keyed by
    # symbol. JSON in .env, e.g.:
    #   RISK_MARKET_STATS={"BTC/USDT": {"avg_daily_volume": 1000000,
    #   "volatility_bps": 200, "half_spread_bps": 5}}
    risk_market_stats: dict[str, dict[str, float]] = {}
    # Unified risk limits (also available as RISK_* env vars) ----------------
    risk_per_trade_pct: float = 0.02
    risk_per_symbol_pct: float = 0.01
    risk_portfolio_pct: float = 0.03
    risk_daily_loss_pct: float = 0.06
    risk_monthly_loss_pct: float = 0.10
    risk_max_drawdown_pct: float = 0.20
    risk_veto_toxicity: bool = True
    risk_veto_impact: bool = True

    # Execution ---------------------------------------------------------------
    max_slippage_pips: float = 5.0
    max_order_age_sec: float = 30.0
    prop_rules_enabled: bool = True

    # Trading mode / prop (unified from env) --------------------------------
    paper_mode: bool = True
    live_trading_authorized: bool = False
    starting_equity: float = 50000.0
    prop_firm: str = "fundingpips"
    prop_model: str = "flex"
    prop_account_type: str = "evaluation"

    # Broker credentials (unified) -------------------------------------------
    fxcm_api_token: SecretStr | None = Field(default=None, repr=False)
    fxcm_account_id: str | None = None
    deriv_api_token: SecretStr | None = Field(default=None, repr=False)
    deriv_app_id: str = "1089"
    oanda_api_token: SecretStr | None = Field(default=None, repr=False)
    oanda_account_id: str | None = None

    # MT5 -------------------------------------------------------------------
    mt5_magic_number: int = 123456
    mt5_data_folder: str | None = None

    # Market-data symbols / channels (comma-separated in .env) ---------------
    crypto_symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"
    crypto_channels: str = "trade,ticker,book,candle"
    forex_symbols: str = (
        "frxEURUSD,frxGBPUSD,frxUSDJPY,frxAUDUSD,frxUSDCAD,frxNZDUSD,frxEURGBP,frxEURJPY,frxGBPJPY"
    )
    deriv_symbols: str = (
        "frxEURUSD,frxGBPUSD,frxUSDJPY,frxAUDUSD,frxUSDCAD,frxNZDUSD,frxEURGBP,"
        "frxEURJPY,frxGBPJPY,R_10,R_25,R_50,R_75,R_100,cryBTCUSD,cryETHUSD"
    )
    deriv_channels: str = "ticks,candles"
    high_impact_news_times: str | None = None

    # Omega / God-mode multi-provider routing (ADR 0005/0006, Continuity) -----
    # When True the decision pipeline uses SmartFallbackReasoner (Zen -> Groq ->
    # OpenRouter -> Cerebras/Gemini) with instant key rotation and hedged race.
    # Keys are loaded from env (GROQ_API_KEY etc.) and/or the Sagax legacy file
    # ``SAGAX_KEYS_PATH`` (default ~/.config/ati/keys.env, portable; override via
    # SAGAX_KEYS_PATH env var). Never hardcode a key; never commit api_keys.env.
    omega_enabled: bool = False
    omega_race_mode: str = "sequential"  # sequential | parallel | hedged
    omega_timeout_seconds: float = 12.0
    sagax_keys_path: str | None = None

    # Execution policy (ADR 0029) ---------------------------------------------
    # "always_market" = baseline (current behavior); "passive_if_spread_tight" =
    # post-only limit when spread is tight, fallback to market.
    execution_policy: str = "always_market"

    # Logging ---------------------------------------------------------------
    log_level: str = "INFO"

    # Provider key pools (also consumed by sagax_loader; declared here so
    # extra="forbid" does not reject them when present in .env / env)
    groq_api_key: str | None = Field(default=None, repr=False)
    openrouter_api_key: str | None = Field(default=None, repr=False)
    gemini_api_key: str | None = Field(default=None, repr=False)
    cerebras_api_key: str | None = Field(default=None, repr=False)
    agentrouter_api_key: str | None = Field(default=None, repr=False)
    opencode_zen_api_key: str | None = Field(default=None, repr=False)
    mexc_api_key: str | None = Field(default=None, repr=False)
    mexc_api_secret: str | None = Field(default=None, repr=False)
    mt5_login: int | None = None
    mt5_password: str | None = Field(default=None, repr=False)
    mt5_server: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )


settings = Settings()
