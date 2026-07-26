from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class RuleModel(BaseModel):
    """Base class for structured rule data used by the deterministic engine."""

    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


class TariffProductMetadata(RuleModel):
    pct_code: str
    display_pct_code: str | None = None
    simple_product_name: str
    official_tariff_description: str
    tariff_source_page: int | None = Field(default=None, ge=1)
    source_document: str | None = None
    source_url: str | None = None
    issuing_authority: str | None = None
    effective_date: date | None = None
    validation_status: str | None = None


class PctConfiguration(RuleModel):
    pct_codes: list[str]
    products: list[TariffProductMetadata]


class RegulatoryRequirement(RuleModel):
    value: bool | str | None = None
    verification_status: str | None = None
    validation_status: str | None = None
    details: str | None = None
    note: str | None = None

    @property
    def resolved_validation_status(self) -> str | None:
        return self.verification_status or self.validation_status


class ExportStatusRule(RuleModel):
    value: str | None = None
    verification_status: str | None = None
    note: str | None = None


class CommonExportClearanceRule(RuleModel):
    responsible_government_agency: str | None = None
    procedure: str | None = None
    supporting_documents: list[str]
    source_url: str | None = None
    source_locator: str | None = None
    effective_date: date | None = None
    validation_status: str | None = None


class DestinationProcedureRule(RuleModel):
    destination_or_scheme: str
    certificate_required: bool | str
    supporting_documents: list[str] = Field(default_factory=list)
    fee: str | None = None
    processing_time: str | None = None
    source_url: str | None = None
    source_locator: str | None = None
    issuing_authority: str | None = None
    effective_date: date | None = None
    validation_status: str | None = None


class CertificateOfOriginRule(RuleModel):
    applies_to_selected_codes: list[str]
    does_not_list_selected_raw_cotton_commodity: str | None = None
    responsible_government_agency: str | None = None
    requirement: str | None = None
    legal_basis: list[str] = Field(default_factory=list)
    destination_procedures: list[DestinationProcedureRule] = Field(
        default_factory=list
    )
    measure_source_url: str | None = None
    procedure_index_url: str | None = None
    source_locator: str | None = None
    effective_date: date | None = None
    validation_status: str | None = None


class ProductRequirementRule(RuleModel):
    pct_code: str
    display_pct_code: str | None = None
    tipp_commodity_code: str | None = None
    simple_product_name: str
    export_status: ExportStatusRule | None = None
    responsible_government_agencies: list[str] = Field(default_factory=list)
    licence_required: RegulatoryRequirement | None = None
    permit_required: RegulatoryRequirement | None = None
    certificate_required: RegulatoryRequirement | None = None
    approval_required: RegulatoryRequirement | None = None
    legal_basis: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    local_legal_sources: list[str] = Field(default_factory=list)
    source_page: int | None = Field(default=None, ge=1)
    source_locator: str | None = None
    issuing_authority: str | None = None
    effective_date: date | None = None
    retrieval_date: date | None = None


class ProductRequirementsDocument(RuleModel):
    schema_version: str
    retrieval_date: date
    common_export_clearance: CommonExportClearanceRule
    conditional_certificate_of_origin: CertificateOfOriginRule
    products: list[ProductRequirementRule]


class ComplianceRuleSet(RuleModel):
    supported_codes: frozenset[str]
    product_metadata: dict[str, TariffProductMetadata]
    product_requirements: dict[str, ProductRequirementRule]
    common_export_clearance: CommonExportClearanceRule
    conditional_certificate_of_origin: CertificateOfOriginRule
    pct_config_source: str
    product_requirements_source: str
    rule_data_version: str
