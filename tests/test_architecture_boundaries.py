"""AST-based architecture boundary tests (no extra architecture library)."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"

FORBIDDEN_DOMAIN_MODULES = {
    "mcp",
    "sqlalchemy",
    "alembic",
    "pydantic_settings",
    "pydantic",
    "dotenv",
    "uuid6",
}

LAYER_ROOTS = {
    "domain": SRC / "domain",
    "application": SRC / "application",
    "infrastructure": SRC / "infrastructure",
    "interfaces": SRC / "interfaces",
}


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _top_level(mod: str) -> str:
    return mod.split(".", 1)[0]


def _is_module(imp: str, name: str) -> bool:
    return imp == name or imp.startswith(f"{name}.")


def test_domain_has_no_framework_or_outer_layer_imports() -> None:
    violations: list[str] = []
    for path in _iter_py_files(LAYER_ROOTS["domain"]):
        for imp in _imports(path):
            top = _top_level(imp)
            if top in FORBIDDEN_DOMAIN_MODULES:
                violations.append(f"{path}: imports {imp}")
            if _is_module(imp, "application"):
                violations.append(f"{path}: imports application ({imp})")
            if _is_module(imp, "infrastructure"):
                violations.append(f"{path}: imports infrastructure ({imp})")
            if _is_module(imp, "interfaces"):
                violations.append(f"{path}: imports interfaces ({imp})")
            if _is_module(imp, "bootstrap"):
                violations.append(f"{path}: imports bootstrap ({imp})")
    assert not violations, "Domain boundary violations:\n" + "\n".join(violations)


def test_domain_providers_cache_key_is_framework_free() -> None:
    """domain.providers.cache_key may only depend on domain.common + stdlib."""
    providers_root = LAYER_ROOTS["domain"] / "providers"
    assert providers_root.is_dir(), "domain/providers must exist"
    allowed_tops = {
        "__future__",
        "domain",
        "re",
        "dataclasses",
        "datetime",
        "typing",
        "collections",
    }
    violations: list[str] = []
    for path in _iter_py_files(providers_root):
        for imp in _imports(path):
            top = _top_level(imp)
            if top in FORBIDDEN_DOMAIN_MODULES:
                violations.append(f"{path}: imports framework {imp}")
            if _is_module(imp, "application") or _is_module(imp, "infrastructure"):
                violations.append(f"{path}: imports outer layer {imp}")
            # providers may import domain.providers / domain.common only
            if (
                _is_module(imp, "domain")
                and not _is_module(imp, "domain.common")
                and not _is_module(imp, "domain.providers")
            ):
                violations.append(
                    f"{path}: domain.providers may only import domain.common (or itself), got {imp}"
                )
            # Allow domain.* + listed stdlib only; anything else is unexpected.
            if top not in allowed_tops and top not in FORBIDDEN_DOMAIN_MODULES and top != "domain":
                violations.append(f"{path}: unexpected import {imp}")
    assert not violations, "domain.providers boundary violations:\n" + "\n".join(violations)


def test_provider_cache_store_does_not_import_application_services() -> None:
    """SqlAlchemyProviderCacheStore must use domain cache-key helpers, not services."""
    store = LAYER_ROOTS["infrastructure"] / "persistence" / "provider_cache_store.py"
    assert store.is_file()
    imports = _imports(store)
    service_imports = [i for i in imports if _is_module(i, "application.services")]
    assert not service_imports, (
        f"provider_cache_store must not import application.services: {service_imports}"
    )
    domain_cache_imports = [i for i in imports if _is_module(i, "domain.providers.cache_key")]
    assert domain_cache_imports, "provider_cache_store must import domain.providers.cache_key"


def test_application_does_not_import_infrastructure_or_interfaces() -> None:
    violations: list[str] = []
    for path in _iter_py_files(LAYER_ROOTS["application"]):
        for imp in _imports(path):
            if _is_module(imp, "infrastructure"):
                violations.append(f"{path}: imports infrastructure ({imp})")
            if _is_module(imp, "interfaces"):
                violations.append(f"{path}: imports interfaces ({imp})")
            if _is_module(imp, "bootstrap"):
                violations.append(f"{path}: imports bootstrap ({imp})")
            if _top_level(imp) in {"mcp", "sqlalchemy", "alembic", "pydantic_settings"}:
                violations.append(f"{path}: imports framework {imp}")
    assert not violations, "Application boundary violations:\n" + "\n".join(violations)


def test_interfaces_do_not_import_infrastructure() -> None:
    violations: list[str] = []
    for path in _iter_py_files(LAYER_ROOTS["interfaces"]):
        for imp in _imports(path):
            if _is_module(imp, "infrastructure"):
                violations.append(f"{path}: imports infrastructure ({imp})")
    assert not violations, "Interfaces boundary violations:\n" + "\n".join(violations)


def test_only_bootstrap_is_composition_root() -> None:
    """Only bootstrap may import application services and infrastructure providers."""
    violations: list[str] = []
    bootstrap = SRC / "bootstrap.py"
    assert bootstrap.is_file()

    for path in _iter_py_files(SRC):
        if path == bootstrap:
            continue
        # tests and migrations not under src
        imports = _imports(path)
        has_app = any(
            _is_module(i, "application.services") or _is_module(i, "application") for i in imports
        )
        has_infra = any(_is_module(i, "infrastructure") for i in imports)
        # interfaces may import application + bootstrap container type only
        if path.is_relative_to(LAYER_ROOTS["interfaces"]):
            if has_infra:
                violations.append(f"{path}: interface imports infrastructure")
            continue
        if path.is_relative_to(LAYER_ROOTS["domain"]):
            continue
        if path.is_relative_to(LAYER_ROOTS["application"]):
            continue
        if path.is_relative_to(LAYER_ROOTS["infrastructure"]):
            # infrastructure may import application ports/dto only — never services.
            for imp in imports:
                if _is_module(imp, "application.services"):
                    violations.append(f"{path}: infrastructure imports application services")
            continue
        # other modules under src (should only be bootstrap.py at top level)
        if has_app and has_infra and path.name != "bootstrap.py":
            violations.append(f"{path}: non-bootstrap composition of app+infra")

    # bootstrap itself must import both
    boot_imports = _imports(bootstrap)
    assert any(_is_module(i, "application") for i in boot_imports)
    assert any(_is_module(i, "infrastructure") for i in boot_imports)
    assert not violations, "Composition root violations:\n" + "\n".join(violations)


def test_composition_root_and_orm_modules_stay_bounded() -> None:
    """Prevent the two refactored hotspots from collapsing back into monoliths."""
    from bootstrap import ApplicationContainer

    bootstrap = SRC / "bootstrap.py"
    # Optional account, notification, weekend-reference, and bounded Monitor-LLM
    # wiring remains explicit in the sole composition root. Keep a tight ceiling
    # without forcing infrastructure factories to import application services.
    assert len(bootstrap.read_text(encoding="utf-8").splitlines()) <= 1_050
    assert set(ApplicationContainer.__dataclass_fields__) == {
        "settings",
        "context",
        "resources",
        "providers",
        "services",
        "operations",
    }

    composition_root = LAYER_ROOTS["infrastructure"] / "composition"
    for path in composition_root.glob("*.py"):
        assert all(
            not _is_module(imp, "application.services") for imp in _imports(path)
        ), path

    persistence_root = LAYER_ROOTS["infrastructure"] / "persistence"
    assert not (persistence_root / "models.py").exists()
    orm_root = persistence_root / "orm"
    declaration_modules = [
        path for path in orm_root.glob("*.py") if path.name not in {"__init__.py", "common.py"}
    ]
    assert len(declaration_modules) == 11
    largest_module = max(
        len(path.read_text(encoding="utf-8").splitlines()) for path in declaration_modules
    )
    assert largest_module <= 650


def test_no_forbidden_phase_modules() -> None:
    forbidden_names = {
        "strategies",
        "backtest",
        "execution",
        "orders",
        "fills",
    }
    found: list[str] = []
    for path in SRC.rglob("*"):
        if path.name in forbidden_names or path.stem in forbidden_names:
            found.append(str(path.relative_to(PROJECT_ROOT)))
    assert not found, f"Forbidden Phase 2+ modules present: {found}"


def test_interfaces_mcp_imports_application_not_domain_models_for_response() -> None:
    """MCP adapters return DTOs through application services, not domain dumps."""
    server = LAYER_ROOTS["interfaces"] / "mcp" / "server.py"
    tool_root = LAYER_ROOTS["interfaces"] / "mcp" / "tools"
    server_text = server.read_text(encoding="utf-8")
    adapter_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(tool_root.glob("*.py"))
    )
    assert "bootstrap" in server_text or "ApplicationContainer" in server_text
    assert "model_dump" in adapter_text


def test_mcp_lifecycle_and_public_inventory_stay_thin() -> None:
    """Tool growth belongs in compact adapters, not lifecycle or inventory façades."""
    mcp_root = LAYER_ROOTS["interfaces"] / "mcp"
    server = mcp_root / "server.py"
    inventory = mcp_root / "tool_inventory.py"
    tool_root = mcp_root / "tools"
    adapter_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(tool_root.glob("*.py"))
    )
    assert len(server.read_text(encoding="utf-8").splitlines()) <= 100
    assert len(inventory.read_text(encoding="utf-8").splitlines()) <= 100
    assert "application.dto" not in server.read_text(encoding="utf-8")
    assert "@server.tool" not in server.read_text(encoding="utf-8")
    assert not (tool_root / "handler_registry.py").exists()
    assert not (tool_root / "types.py").exists()
    assert "HandlerRegistry" not in adapter_text
    assert "ToolRegistrar" not in adapter_text
    assert "@server.tool" not in adapter_text


def test_large_a_share_adapters_and_codecs_have_stable_facades() -> None:
    """Provider and codec implementations stay physically capability-split."""
    provider_root = LAYER_ROOTS["infrastructure"] / "providers" / "a_share"
    eastmoney = provider_root / "eastmoney"
    sina = provider_root / "sina"
    codecs = provider_root / "codecs"

    assert (eastmoney / "client.py").is_file()
    assert (eastmoney / "quote_bars.py").is_file()
    assert (eastmoney / "fundamentals.py").is_file()
    assert (eastmoney / "capital.py").is_file()
    assert (eastmoney / "sentiment.py").is_file()
    assert (sina / "client.py").is_file()
    assert (sina / "daily_flow.py").is_file()
    assert (sina / "financials.py").is_file()
    assert (sina / "options.py").is_file()
    assert not (codecs / "typed.py").exists()
    for capability in ("market", "research", "capital", "sentiment", "options"):
        text = (codecs / f"{capability}.py").read_text(encoding="utf-8")
        assert "def " in text
        assert "codecs.typed import" not in text


def test_a_share_domain_models_stay_capability_split() -> None:
    """Capability models and shared validators must not collapse into the façade."""
    from domain.a_share import (
        calendar_models,
        capital_models,
        fundamental_models,
        industry_models,
        market_context_models,
        market_models,
        models,
        research_models,
        signal_option_models,
    )

    domain_root = LAYER_ROOTS["domain"] / "a_share"
    facade = domain_root / "models.py"
    calendar = domain_root / "calendar_models.py"
    capital = domain_root / "capital_models.py"
    fundamentals = domain_root / "fundamental_models.py"
    industry = domain_root / "industry_models.py"
    market_context = domain_root / "market_context_models.py"
    market = domain_root / "market_models.py"
    research = domain_root / "research_models.py"
    signal_option = domain_root / "signal_option_models.py"
    validation = domain_root / "model_validation.py"

    assert calendar.is_file()
    assert capital.is_file()
    assert fundamentals.is_file()
    assert industry.is_file()
    assert market_context.is_file()
    assert market.is_file()
    assert research.is_file()
    assert signal_option.is_file()
    assert validation.is_file()
    assert len(facade.read_text(encoding="utf-8").splitlines()) <= 100
    assert len(calendar.read_text(encoding="utf-8").splitlines()) <= 50
    assert len(capital.read_text(encoding="utf-8").splitlines()) <= 450
    assert len(fundamentals.read_text(encoding="utf-8").splitlines()) <= 150
    assert len(industry.read_text(encoding="utf-8").splitlines()) <= 350
    assert len(market_context.read_text(encoding="utf-8").splitlines()) <= 120
    assert len(market.read_text(encoding="utf-8").splitlines()) <= 250
    assert len(research.read_text(encoding="utf-8").splitlines()) <= 220
    assert len(signal_option.read_text(encoding="utf-8").splitlines()) <= 450
    assert len(validation.read_text(encoding="utf-8").splitlines()) <= 350
    facade_text = facade.read_text(encoding="utf-8")
    assert "class IndustryMetricObservation:" not in facade_text
    assert models.IndustryMetricObservation is industry_models.IndustryMetricObservation
    assert (
        models.CompanyOperatingMetricsSnapshot
        is industry_models.CompanyOperatingMetricsSnapshot
    )
    assert models.AShareQuote is market_models.AShareQuote
    assert models.AShareBar is market_models.AShareBar
    assert models.validate_order_book_levels is market_models.validate_order_book_levels
    assert models.FinancialStatementLine is fundamental_models.FinancialStatementLine
    assert models.AnalystReportItem is research_models.AnalystReportItem
    assert models.MarketBoardSnapshot is market_context_models.MarketBoardSnapshot
    assert models.ChipDistributionSnapshot is capital_models.ChipDistributionSnapshot
    assert models.LimitUpContext is signal_option_models.LimitUpContext
    assert models.TradingSessionWindow is calendar_models.TradingSessionWindow


def test_a_share_snapshot_validation_stays_out_of_orchestration_service() -> None:
    """Snapshot orchestration must not re-absorb strict Provider validation."""
    services = LAYER_ROOTS["application"] / "services"
    service = services / "a_share_snapshot_service.py"
    validation = services / "a_share_snapshot_validation.py"

    assert validation.is_file()
    assert len(service.read_text(encoding="utf-8").splitlines()) <= 1_100
    assert len(validation.read_text(encoding="utf-8").splitlines()) <= 550
    service_text = service.read_text(encoding="utf-8")
    assert "class AShareSnapshotService(AShareSnapshotValidationMixin):" in service_text
    assert "def _validate_fundamentals(" not in service_text


def test_a_share_dtos_stay_capability_split() -> None:
    """A-share wire models keep one stable façade without returning to a monolith."""
    from application.dto import (
        a_share,
        a_share_core_outputs,
        a_share_inputs,
        a_share_market_outputs,
        a_share_product_outputs,
        a_share_signal_outputs,
    )

    dto_root = LAYER_ROOTS["application"] / "dto"
    caps = {
        "a_share.py": 100,
        "a_share_common.py": 80,
        "a_share_inputs.py": 550,
        "a_share_core_outputs.py": 350,
        "a_share_market_outputs.py": 320,
        "a_share_signal_outputs.py": 220,
        "a_share_product_outputs.py": 400,
    }
    for filename, line_cap in caps.items():
        path = dto_root / filename
        assert path.is_file()
        assert len(path.read_text(encoding="utf-8").splitlines()) <= line_cap

    facade_text = (dto_root / "a_share.py").read_text(encoding="utf-8")
    assert "class AShareGetSnapshotInput(" not in facade_text
    assert "class AShareCompositeSnapshotDTO(" not in facade_text
    assert a_share.AShareGetSnapshotInput is a_share_inputs.AShareGetSnapshotInput
    assert a_share.AShareQuoteDTO is a_share_core_outputs.AShareQuoteDTO
    assert a_share.ChipDistributionSnapshotDTO is (
        a_share_market_outputs.ChipDistributionSnapshotDTO
    )
    assert a_share.EtfOptionSnapshotDTO is a_share_signal_outputs.EtfOptionSnapshotDTO
    assert a_share.IndustryCycleSnapshotDTO is a_share_product_outputs.IndustryCycleSnapshotDTO


def test_d6b1_ports_stay_in_application_without_infrastructure() -> None:
    """Cache codec / engine ports are application Protocols only."""
    for rel in (
        ("ports", "provider_cache_codec.py"),
        ("ports", "provider_router_engine.py"),
    ):
        path = LAYER_ROOTS["application"].joinpath(*rel)
        for imp in _imports(path):
            assert not _is_module(imp, "infrastructure"), (path, imp)
            assert not _is_module(imp, "interfaces"), (path, imp)
            assert not _is_module(imp, "bootstrap"), (path, imp)


def test_d6b2_facade_does_not_import_infrastructure() -> None:
    """ProviderRouter is application-only; must not import infrastructure."""
    path = LAYER_ROOTS["application"] / "services" / "provider_router.py"
    for imp in _imports(path):
        assert not _is_module(imp, "infrastructure"), (path, imp)
        assert not _is_module(imp, "interfaces"), (path, imp)
        assert not _is_module(imp, "bootstrap"), (path, imp)


def test_d6b2_settings_port_stays_in_application() -> None:
    """ProviderRouterSettings Protocol must not depend on infrastructure Settings."""
    path = LAYER_ROOTS["application"] / "ports" / "provider_router_settings.py"
    for imp in _imports(path):
        assert not _is_module(imp, "infrastructure"), (path, imp)
        assert not _is_module(imp, "interfaces"), (path, imp)
        assert not _is_module(imp, "bootstrap"), (path, imp)
    text = path.read_text(encoding="utf-8")
    assert "AppSettings" not in text
    assert "pydantic" not in text


def test_d6b2_engine_does_not_import_application_services() -> None:
    """Engine may use ports/dto + resilience helpers; never application services."""
    path = LAYER_ROOTS["infrastructure"] / "providers" / "router_engine.py"
    imports = _imports(path)
    service_imports = [i for i in imports if _is_module(i, "application.services")]
    assert not service_imports, service_imports
    assert any(_is_module(i, "application.ports") for i in imports)
    assert any(_is_module(i, "application.dto") for i in imports)
    text = path.read_text(encoding="utf-8")
    for forbidden in (
        "import pickle",
        "from pickle",
        "import marshal",
        "from marshal",
        "default=str",
        "AppSettings",
    ):
        assert forbidden not in text, f"forbidden in engine: {forbidden}"


def test_d7_domain_modules_stay_framework_free() -> None:
    """D7 pure rules may only import domain + stdlib (no settings/sqlalchemy/mcp)."""
    paths = [
        LAYER_ROOTS["domain"] / "market" / "freshness.py",
        LAYER_ROOTS["domain"] / "market" / "session.py",
        LAYER_ROOTS["domain"] / "market" / "stale_guard.py",
        LAYER_ROOTS["domain"] / "common" / "as_of.py",
    ]
    allowed_tops = {
        "__future__",
        "domain",
        "dataclasses",
        "datetime",
        "typing",
        "collections",
        "zoneinfo",
    }
    violations: list[str] = []
    for path in paths:
        for imp in _imports(path):
            top = _top_level(imp)
            if top in FORBIDDEN_DOMAIN_MODULES:
                violations.append(f"{path}: imports framework {imp}")
            if _is_module(imp, "application") or _is_module(imp, "infrastructure"):
                violations.append(f"{path}: imports outer layer {imp}")
            if _is_module(imp, "interfaces") or _is_module(imp, "bootstrap"):
                violations.append(f"{path}: imports outer layer {imp}")
            if top not in allowed_tops and not _is_module(imp, "domain"):
                violations.append(f"{path}: unexpected import {imp}")
    assert not violations, "D7 domain purity violations:\n" + "\n".join(violations)


def test_vendor_registry_imports_ports_not_application_services() -> None:
    """VendorRegistry may use CategoryProvider port; never application services."""
    path = LAYER_ROOTS["infrastructure"] / "providers" / "registry.py"
    imports = _imports(path)
    assert any(_is_module(i, "application.ports") for i in imports), (
        "registry must import application.ports.category_provider"
    )
    service_imports = [i for i in imports if _is_module(i, "application.services")]
    assert not service_imports, f"registry must not import application.services: {service_imports}"


def test_domain_market_validation_imports_only_domain_or_stdlib() -> None:
    """Authoritative validate_verified_market_snapshot lives in domain only."""
    path = LAYER_ROOTS["domain"] / "market" / "validation.py"
    assert path.is_file(), "domain/market/validation.py must exist"
    allowed_tops = {
        "__future__",
        "domain",
        "decimal",
        "dataclasses",
        "datetime",
        "typing",
        "collections",
    }
    imports = _imports(path)
    violations: list[str] = []
    for imp in imports:
        top = _top_level(imp)
        if top in FORBIDDEN_DOMAIN_MODULES:
            violations.append(f"framework {imp}")
        if _is_module(imp, "application") or _is_module(imp, "infrastructure"):
            violations.append(f"outer layer {imp}")
        if _is_module(imp, "interfaces") or _is_module(imp, "bootstrap"):
            violations.append(f"outer layer {imp}")
        if top not in allowed_tops and top not in FORBIDDEN_DOMAIN_MODULES:
            violations.append(f"unexpected import {imp}")
    assert not violations, "domain.market.validation boundary violations:\n" + "\n".join(violations)
    # Must define the public validator symbol.
    text = path.read_text(encoding="utf-8")
    assert "def validate_verified_market_snapshot" in text


def test_criticality_policy_stays_in_application() -> None:
    """CriticalityPolicy is application-only; must not import infrastructure."""
    path = LAYER_ROOTS["application"] / "services" / "criticality_policy.py"
    for imp in _imports(path):
        assert not _is_module(imp, "infrastructure"), imp
        assert not _is_module(imp, "interfaces"), imp


def test_d8b_provider_state_backend_does_not_import_application_services() -> None:
    """Backend selection stays in infrastructure; no application.services."""
    for rel in (
        ("persistence", "in_memory_provider_state.py"),
        ("persistence", "provider_state_backend.py"),
    ):
        path = LAYER_ROOTS["infrastructure"].joinpath(*rel)
        imports = _imports(path)
        service_imports = [i for i in imports if _is_module(i, "application.services")]
        assert not service_imports, (path, service_imports)
        for imp in imports:
            assert not _is_module(imp, "bootstrap"), (path, imp)
            assert not _is_module(imp, "interfaces"), (path, imp)


def test_d8b_bootstrap_wires_router_fields() -> None:
    """Composition root exposes the shared provider router and registry."""
    path = SRC / "bootstrap.py"
    text = path.read_text(encoding="utf-8")
    assert "provider_router" in text
    assert "vendor_registry" in text
    assert "build_provider_infrastructure" in text
    provider_composition = (
        LAYER_ROOTS["infrastructure"] / "composition" / "providers.py"
    ).read_text(encoding="utf-8")
    assert "build_provider_state_backend" in provider_composition
    assert "YamlVendorChainConfig" in provider_composition
    assert "ProviderRouterEngine" in provider_composition
    # Must not run migrations or seed in build_application (imports / call sites).
    assert "command.upgrade" not in text
    imports = _imports(path)
    assert not any(_is_module(i, "alembic") for i in imports)
    assert not any(
        _is_module(i, "infrastructure.persistence.instrument_seed_loader") for i in imports
    )


# --- Phase 1C C2a layout / boundary anti-regression ---


_C2A_BUSINESS_ROW_NAMES = (
    "ResearchEvidenceRow",
    "SubjectEvidenceLinkRow",
    "EvidenceAssessmentRow",
    "ResearchReportRow",
    "ResearchEventRow",
    "DecisionRecordRow",
    "JournalEntryRow",
)


def test_public_tool_surface_respects_architecture_boundary() -> None:
    """The default façade excludes internal and retired write surfaces."""
    from interfaces.mcp.server import (
        FORBIDDEN_PUBLIC_TOOL_NAMES,
        PUBLIC_TOOL_NAMES,
        RETIRED_PUBLIC_TOOL_NAMES,
    )

    assert len(PUBLIC_TOOL_NAMES) == 28
    assert PUBLIC_TOOL_NAMES.isdisjoint(FORBIDDEN_PUBLIC_TOOL_NAMES)
    assert PUBLIC_TOOL_NAMES.isdisjoint(RETIRED_PUBLIC_TOOL_NAMES)
    tool_root = LAYER_ROOTS["interfaces"] / "mcp" / "tools"
    adapter_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(tool_root.glob("*.py"))
    )
    for forbidden in FORBIDDEN_PUBLIC_TOOL_NAMES | RETIRED_PUBLIC_TOOL_NAMES:
        assert f'name="{forbidden}"' not in adapter_text
    bootstrap = (PROJECT_ROOT / "src" / "bootstrap.py").read_text(encoding="utf-8")
    for field in (
        "research_archive_service",
        "research_search_service",
        "research_timeline_service",
        "journal_service",
        "decision_record_service",
        "us_tool_coordinator",
    ):
        assert field in bootstrap
    persistence_composition = (
        LAYER_ROOTS["infrastructure"] / "composition" / "persistence.py"
    ).read_text(encoding="utf-8")
    assert "search_backend_probe" in persistence_composition


def test_c3_uow_exposes_search_index_with_business_properties() -> None:
    """Research UoW protocol/concrete expose C2b properties plus search_index."""
    port = LAYER_ROOTS["application"] / "ports" / "research_unit_of_work.py"
    concrete = LAYER_ROOTS["infrastructure"] / "persistence" / "research_unit_of_work.py"
    port_text = port.read_text(encoding="utf-8")
    concrete_text = concrete.read_text(encoding="utf-8")
    for name in (
        "evidence",
        "subject_evidence_links",
        "evidence_assessments",
        "reports",
        "events",
        "decisions",
        "journal",
        "search_index",
    ):
        assert f"def {name}(self)" in port_text or f"def {name}(" in port_text, name
        assert f"def {name}(self)" in concrete_text, name
    assert "SqlAlchemyResearchSearchIndex" in concrete_text
    assert "ResearchSearchIndex" in port_text


def test_c3_search_index_uses_core_sql_without_orm_rows() -> None:
    """Search projection must not introduce ORM Row classes or full-table Python scan."""
    impl = (
        LAYER_ROOTS["infrastructure"] / "persistence" / "repositories" / "research_search_index.py"
    )
    text = impl.read_text(encoding="utf-8")
    for forbidden in (
        "class ResearchSearchDocumentRow",
        "class ResearchSearchDocumentCaseRow",
        "class ResearchSearchDocumentInstrumentRow",
        "class ResearchSearchDocumentTagRow",
        "class ResearchSearchFtsRow",
        '__tablename__ = "research_search_documents"',
        "LIKE '%",
        "instrument_ids_text LIKE",
    ):
        assert forbidden not in text, f"forbidden pattern present: {forbidden}"
    assert "build_fts_match_query" in text
    assert "bm25" in text
    assert "SearchBackendUnavailable" in text


def test_c2a_business_orm_rows_only_no_search_rows() -> None:
    """Exactly seven frozen business ORM rows; no Search document/FTS ORM rows."""
    root = LAYER_ROOTS["infrastructure"] / "persistence" / "orm"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    for name in _C2A_BUSINESS_ROW_NAMES:
        assert f"class {name}(" in text, f"missing ORM row {name}"
    for forbidden in (
        "class ResearchSearchDocumentRow",
        "class ResearchSearchDocumentCaseRow",
        "class ResearchSearchDocumentInstrumentRow",
        "class ResearchSearchDocumentTagRow",
        "class ResearchSearchFtsRow",
        "class SearchDocumentRow",
    ):
        assert forbidden not in text, f"forbidden Search ORM row present: {forbidden}"
    # Table names for search must not be registered as ORM __tablename__
    for table in (
        "research_search_documents",
        "research_search_document_cases",
        "research_search_document_instruments",
        "research_search_document_tags",
        "research_search_fts",
    ):
        assert f'__tablename__ = "{table}"' not in text


def test_c2a_models_stay_in_infrastructure_without_outer_layers() -> None:
    """Phase 1C ORM models may use SQLAlchemy + local metadata only."""
    root = LAYER_ROOTS["infrastructure"] / "persistence" / "orm"
    for path in root.glob("*.py"):
        imports = _imports(path)
        for imp in imports:
            assert not _is_module(imp, "application.services"), (path, imp)
            assert not _is_module(imp, "interfaces"), (path, imp)
            assert not _is_module(imp, "bootstrap"), (path, imp)
            assert not _is_module(imp, "mcp"), (path, imp)
        domain_imports = [i for i in imports if _is_module(i, "domain")]
        assert not domain_imports, (path, domain_imports)


def test_c2a_migration_file_is_sqlite_safe_and_probes_fts5() -> None:
    """0004 must probe FTS5, create triggers, and avoid Base.metadata.create_all."""
    path = PROJECT_ROOT / "migrations" / "versions" / "0004_phase1c_research_memory.py"
    text = path.read_text(encoding="utf-8")
    assert 'revision: str = "0004_phase1c_research_memory"' in text
    assert "down_revision" in text
    assert "0003_phase1d_instrument_provider" in text
    assert "sqlite_compileoption_used('ENABLE_FTS5')" in text
    assert "CREATE VIRTUAL TABLE research_search_fts USING fts5" in text
    assert "research_search_documents_ai" in text
    assert "research_search_documents_ad" in text
    assert "research_search_documents_au" in text
    assert "phase1c_research_memory" in text
    assert "Base.metadata.create_all" not in text
    assert "create_all(" not in text


# --- Layout / packaging anti-regression (no trading_partner package layer) ---

CACHE_DIR_NAMES = frozenset({"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"})

ALLOWED_SRC_TOP_LEVEL = frozenset(
    {
        "bootstrap.py",
        "application",
        "domain",
        "infrastructure",
        "interfaces",
    }
)

REQUIRED_ROOT_MARKDOWN = frozenset({"README.md", "AGENTS.md", "SECURITY.md"})


def test_src_trading_partner_package_does_not_exist() -> None:
    """Imports are top-level; there is no trading_partner package layer under src."""
    assert not (SRC / "trading_partner").exists()


def test_src_top_level_layout_is_exactly_allowed() -> None:
    """src may only contain bootstrap.py and the four layer packages (ignore caches)."""
    assert SRC.is_dir()
    actual = {
        entry.name
        for entry in SRC.iterdir()
        if entry.name not in CACHE_DIR_NAMES and not entry.name.startswith(".")
    }
    assert actual == ALLOWED_SRC_TOP_LEVEL, (
        f"Unexpected src top-level entries: {sorted(actual - ALLOWED_SRC_TOP_LEVEL)}; "
        f"missing: {sorted(ALLOWED_SRC_TOP_LEVEL - actual)}"
    )


def test_project_root_markdown_is_exactly_readme_and_agents() -> None:
    """Root-level markdown is limited to public entry-point documents."""
    actual = {path.name for path in PROJECT_ROOT.glob("*.md")}
    assert actual == REQUIRED_ROOT_MARKDOWN, (
        f"Unexpected root *.md: {sorted(actual - REQUIRED_ROOT_MARKDOWN)}; "
        f"missing: {sorted(REQUIRED_ROOT_MARKDOWN - actual)}"
    )
