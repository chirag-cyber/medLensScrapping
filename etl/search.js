/**
 * Search Engine Module
 */

const { Medicine, Price } = require("./load/models");
const { normalizeName, normalizeSalt, extractDosage } = require("./transform/transformer");

async function searchMedicines(queryStr) {
  if (!queryStr) return [];

  // Normalize the query so we run it against canonical data
  const normalizedQuery = normalizeName(queryStr);
  const dosageMatch = extractDosage(queryStr);

  try {
    // We match on anything that is text matched to normalize_name OR normalize_salt
    // Note: To use proper text search, $text requires building a text index string,
    // but simpler regex matching works great for smaller DBs too. We'll use text search:
    
    // First Priority: Name match
    let medicines = await Medicine.find({
      $text: { $search: normalizedQuery }
    }).limit(10);

    // Second Priority: Salt match (if name yields nothing)
    if (medicines.length === 0) {
      const dbQuery = { normalized_salt: { $regex: new RegExp(normalizeSalt(queryStr), 'i') } };
      if (dosageMatch) dbQuery.dosage = dosageMatch;

      medicines = await Medicine.find(dbQuery).limit(10);
    }

    if (!medicines || medicines.length === 0) return [];

    // For the primary hit, gather pricing data
    const results = [];

    for (const med of medicines) {
      // Find prices linked to this Medicine ID
      const prices = await Price.find({ medicine_id: med._id }, { _id: 0, medicine_id: 0, __v: 0, createdAt: 0 });

      // Find alternatives (other medicines with the exact same salt & dosage)
      let alternativesArr = [];
      if (med.normalized_salt) {
        const altQuery = { 
          normalized_salt: med.normalized_salt, 
          _id: { $ne: med._id } 
        };
        if (med.dosage) altQuery.dosage = med.dosage;

        const alternatives = await Medicine.find(altQuery).limit(3);
        alternativesArr = alternatives.map(a => a.name);
      }

      results.push({
        medicine: med,
        prices: prices,
        alternatives: alternativesArr
      });
    }

    return results;

  } catch (err) {
    console.error("[Search Error]", err);
    return [];
  }
}

module.exports = {
  searchMedicines
};
