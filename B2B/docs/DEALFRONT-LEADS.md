# Dealfront sample leads (for initial testing)

Source: Dealfront B2B database (German companies). User has thousands of rows; start with 3 for end-to-end test.

## Test batch (first 3)

1. **Jan Voß** — (fill from Dealfront export)
2. **Maria Groß** — (fill from Dealfront export)
3. **Mark Rosenberg** — (fill from Dealfront export)

Each row should land in the `B2B Leads` sheet with:
- Company, Industry, Contact name, Role, Email, Phone, Country, Source = "Dealfront"

## Notes

- Most leads are German — English emails still work, but consider industry hook tuning
- Avoid tech jargon; lead with business outcomes (efficiency, revenue, cost reduction)
- Industry hook examples:
  - Energy → grid analytics, demand forecasting
  - Consulting → reporting automation, client dashboards
  - Manufacturing → predictive maintenance, QA vision
  - Retail / eComm → recommendation engines, inventory optimization
  - Logistics → route optimization, document OCR
  - Finance → fraud detection, compliance automation
  - Healthcare → document processing, triage assistance
