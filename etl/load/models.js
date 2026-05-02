const mongoose = require("mongoose");

const faqSchema = new mongoose.Schema(
  {
    question: { type: String, required: true },
    answer: { type: String, required: true },
  },
  { _id: false }
);

// ─── 💊 Medicine Canonical Schema ──────────────────────────────
const medicineSchema = new mongoose.Schema(
  {
    name: { type: String, required: true },
    canonical_key: { type: String, required: true },
    normalized_name: { type: String, required: true },
    salt: { type: String },
    normalized_salt: { type: String },
    primary_salt_key: { type: String },
    salt_tokens: [{ type: String }],
    dosage: { type: String },
    pack_size: { type: String },
    image_url: { type: String },
    manufacturer: { type: String },
    description: { type: String },
    side_effects: [{ type: String }],
    faq: [faqSchema],
    source_platforms: [{ type: String, lowercase: true }],
    last_ingested_at: { type: Date },
    // LLM Enrichment Tracking
    llm_enriched: { type: Boolean, default: false },
    llm_enriched_at: { type: Date },
    llm_enriched_fields: [{ type: String }],
  },
  { timestamps: { createdAt: "createdAt", updatedAt: "updatedAt" } }
);

// Indexes for fast searching / deduplication
medicineSchema.index(
  { canonical_key: 1 },
  {
    unique: true,
    partialFilterExpression: {
      canonical_key: { $exists: true },
    },
  }
);
medicineSchema.index(
  { normalized_name: "text", salt: "text", description: "text" },
  { name: "medicine_text_search" }
);
medicineSchema.index(
  { normalized_name: 1, dosage: 1 },
  { unique: true }
);
medicineSchema.index({ normalized_salt: 1, dosage: 1 });
medicineSchema.index({ primary_salt_key: 1, dosage: 1, pack_size: 1 });
medicineSchema.index({ salt_tokens: 1 });
medicineSchema.index({ source_platforms: 1 });

const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", medicineSchema);

// ─── 💰 Price Platform Schema ────────────────────────────────
const priceSchema = new mongoose.Schema(
  {
    medicine_id: { type: mongoose.Schema.Types.ObjectId, ref: "Medicine", required: true },
    platform: { type: String, required: true, lowercase: true }, // e.g., '1mg', 'pharmeasy'
    price: { type: Number, required: true },
    url: { type: String, required: true },
    source_type: { type: String, default: "product" },
  },
  { timestamps: { createdAt: "createdAt", updatedAt: "last_updated" } }
);

// Compound index to ensure 1 platform entry per medicine (upsert logic depends on this)
priceSchema.index({ medicine_id: 1, platform: 1 }, { unique: true });
priceSchema.index({ platform: 1, url: 1 });

const Price = mongoose.models.Price || mongoose.model("Price", priceSchema);

module.exports = {
  Medicine,
  Price,
};
