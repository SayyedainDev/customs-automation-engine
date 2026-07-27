/**
 * Canonical supporting-document types the backend verifier recognizes
 * (``SupportingDocumentType`` in app/schemas/supporting_documents.py, minus
 * ``unknown``). The value sent to the API must match one of these exactly -
 * the backend resolves aliases, but sending the canonical value directly
 * avoids relying on that.
 */
export const SUPPORTING_DOCUMENT_TYPES: Array<{
  value: string;
  label: string;
  helper: string;
}> = [
  {
    value: "form_e_or_psw_export_declaration",
    label: "Form-E / PSW export declaration",
    helper: "State Bank export declaration filed on the PSW portal",
  },
  {
    value: "certificate_of_origin",
    label: "Certificate of origin",
    helper: "Issued by the Chamber of Commerce",
  },
  {
    value: "sbp_deposit_proof",
    label: "Proof of SBP deposit",
    helper: "Required for raw-cotton shipments",
  },
  {
    value: "sbp_confirmation",
    label: "SBP confirmation",
    helper: "Confirms the deposit reference above",
  },
  {
    value: "irrevocable_letter_of_credit",
    label: "Irrevocable letter of credit",
    helper: "Bank guarantee of payment",
  },
  {
    value: "phytosanitary_certificate",
    label: "Phytosanitary certificate",
    helper: "Required for raw agricultural material",
  },
  {
    value: "importing_country_permit",
    label: "Importing-country permit",
    helper: "Required by some destination countries",
  },
  {
    value: "goods_declaration",
    label: "Goods declaration",
    helper: "Export goods declaration",
  },
  {
    value: "bill_of_lading",
    label: "Bill of lading",
    helper: "Shipping carrier's bill of lading",
  },
  {
    value: "export_contract",
    label: "Export contract",
    helper: "Sales contract with the buyer",
  },
];

export function supportingDocumentLabel(value: string): string {
  return (
    SUPPORTING_DOCUMENT_TYPES.find((type) => type.value === value)?.label ??
    value.replace(/_/g, " ")
  );
}
