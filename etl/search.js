/**
 * Search Engine Module
 */

const { Medicine, Price } = require("./load/models");
const {
  normalizeName,
  normalizeSalt,
  extractDosage,
  extractSaltTokens,
} = require("./transform/transformer");

async function searchMedicines(queryStr) {
  if (!queryStr) return [];

  const normalizedSaltQuery = normalizeSalt(queryStr);
  const dosageMatch = extractDosage(queryStr);
  const saltTokens = extractSaltTokens(queryStr);
  const normalizedQuery = normalizeName(queryStr, { dosage: dosageMatch });

  try {
    let medicines = [];

    if (saltTokens.length > 0) {
      const saltQuery = { salt_tokens: { $in: saltTokens } };
      if (dosageMatch) saltQuery.dosage = dosageMatch;
      medicines = await Medicine.find(saltQuery).sort({ name: 1 }).limit(50).lean();
    }

    if (medicines.length === 0 && normalizedQuery) {
      medicines = await Medicine.find({
        $text: { $search: normalizedQuery }
      }).limit(25).lean();
    }

    if (medicines.length === 0 && normalizedSaltQuery) {
      const dbQuery = {
        $or: [
          { primary_salt_key: { $regex: new RegExp(normalizedSaltQuery, "i") } },
          { normalized_salt: { $regex: new RegExp(normalizedSaltQuery, "i") } },
        ],
      };
      if (dosageMatch) dbQuery.dosage = dosageMatch;

      medicines = await Medicine.find(dbQuery).sort({ name: 1 }).limit(50).lean();
    }

    if (!medicines || medicines.length === 0) return [];

    const medicineIds = medicines.map((medicine) => medicine._id);
    const prices = await Price.find(
      { medicine_id: { $in: medicineIds } },
      { _id: 0, medicine_id: 1, platform: 1, price: 1, url: 1, source_type: 1 },
      { lean: true }
    );

    const pricesByMedicineId = new Map();
    prices.forEach((priceEntry) => {
      const key = String(priceEntry.medicine_id);
      const bucket = pricesByMedicineId.get(key) || [];
      bucket.push({
        platform: priceEntry.platform,
        price: priceEntry.price,
        url: priceEntry.url,
        source_type: priceEntry.source_type,
      });
      pricesByMedicineId.set(key, bucket);
    });

    const medicinesBySaltKey = new Map();
    medicines.forEach((medicine) => {
      const saltKey = medicine.primary_salt_key || medicine.normalized_salt || "";
      if (!saltKey) return;
      const bucket = medicinesBySaltKey.get(saltKey) || [];
      bucket.push(medicine);
      medicinesBySaltKey.set(saltKey, bucket);
    });

    return medicines.map((medicine) => {
      const saltKey = medicine.primary_salt_key || medicine.normalized_salt || "";
      const pricesForMedicine = (pricesByMedicineId.get(String(medicine._id)) || [])
        .slice()
        .sort((left, right) => {
          if (left.price !== right.price) return left.price - right.price;
          return String(left.platform).localeCompare(String(right.platform));
        });
      const pricesByPlatform = pricesForMedicine.reduce((accumulator, priceEntry) => {
        accumulator[priceEntry.platform] = priceEntry;
        return accumulator;
      }, {});
      const alternatives = (medicinesBySaltKey.get(saltKey) || [])
        .filter((candidate) => String(candidate._id) !== String(medicine._id))
        .slice(0, 5)
        .map((candidate) => ({
          name: candidate.name,
          dosage: candidate.dosage,
          pack_size: candidate.pack_size,
          canonical_key: candidate.canonical_key,
        }));

      return {
        medicine,
        prices: pricesForMedicine,
        prices_by_platform: pricesByPlatform,
        platform_count: pricesForMedicine.length,
        lowest_price: pricesForMedicine[0]?.price || null,
        alternatives,
      };
    });

  } catch (err) {
    return [];
  }
}

module.exports = {
  searchMedicines
};
