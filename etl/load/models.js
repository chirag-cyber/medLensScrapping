const mongoose = require("mongoose");

// ─── 💊 Medicine Canonical Schema ──────────────────────────────
const medicineSchema = new mongoose.Schema(
  {
    name: { type: String, required: true },
    normalized_name: { type: String, required: true },
    salt: { type: String },
    normalized_salt: { type: String },
    dosage: { type: String },
  },
  { timestamps: { createdAt: "createdAt", updatedAt: "updatedAt" } }
);

// Indexes for fast searching / deduplication
medicineSchema.index({ normalized_name: "text" }); 
medicineSchema.index({ normalized_salt: 1, dosage: 1 });

const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", medicineSchema);


// ─── 💰 Price Platform Schema ────────────────────────────────
const priceSchema = new mongoose.Schema(
  {
    medicine_id: { type: mongoose.Schema.Types.ObjectId, ref: "Medicine", required: true },
    platform: { type: String, required: true, lowercase: true }, // e.g., '1mg', 'pharmeasy'
    price: { type: Number, required: true },
    url: { type: String, required: true },
  },
  { timestamps: { createdAt: "createdAt", updatedAt: "last_updated" } }
);

// Compound index to ensure 1 platform entry per medicine (upsert logic depends on this)
priceSchema.index({ medicine_id: 1, platform: 1 }, { unique: true });

const Price = mongoose.models.Price || mongoose.model("Price", priceSchema);

module.exports = {
  Medicine,
  Price,
};
