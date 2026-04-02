/**
 * ETL Load/Upsert Layer
 * Inserts or updates Canonical Medicine entries and their Price entries.
 */
const { Medicine, Price } = require("./models");

async function upsertProduct(transformedRecord) {
  if (!transformedRecord.normalized_name && !transformedRecord.normalized_salt) {
    console.warn("Skipping record - no name or salt could be extracted.", transformedRecord.raw_name);
    return null;
  }

  if (isNaN(transformedRecord.price) || typeof transformedRecord.price !== 'number') {
    console.warn("Skipping record - invalid price.", transformedRecord.raw_name);
    return null;
  }

  try {
    // 1. Identify Candidate Medicine
    const query = {
      $or: []
    };

    if (transformedRecord.normalized_name) {
      query.$or.push({ normalized_name: transformedRecord.normalized_name });
    }

    if (transformedRecord.normalized_salt && transformedRecord.dosage) {
      query.$or.push({ 
        normalized_salt: transformedRecord.normalized_salt,
        dosage: transformedRecord.dosage 
      });
    }

    if (query.$or.length === 0) return null;

    let medicine = await Medicine.findOne(query);

    // 2. Create or reuse Medical ID
    if (!medicine) {
      medicine = new Medicine({
        name: transformedRecord.raw_name,
        normalized_name: transformedRecord.normalized_name,
        salt: transformedRecord.raw_salt,
        normalized_salt: transformedRecord.normalized_salt,
        dosage: transformedRecord.dosage,
      });
      await medicine.save();
    }

    // 3. Upsert the Price info connected to the Medicine ID for this platform
    await Price.findOneAndUpdate(
      { 
        medicine_id: medicine._id, 
        platform: transformedRecord.platform 
      },
      {
        $set: {
          price: transformedRecord.price,
          url: transformedRecord.url,
        }
      },
      { upsert: true, new: true }
    );

    return medicine._id;

  } catch (err) {
    console.error("[Upsert Error] Failed to upsert:", transformedRecord.raw_name, err);
    return null;
  }
}

module.exports = {
  upsertProduct
};
