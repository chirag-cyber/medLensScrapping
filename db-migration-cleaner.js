#!/usr/bin/env node

require("dotenv").config();
const mongoose = require("mongoose");
const connectDB = require("./etl/load/db");
const { Medicine } = require("./etl/load/models");
const { cleanMedicineName } = require("./etl/transform/nameCleaner");
const { cleanDescription, cleanSideEffects, cleanFaq } = require("./etl/transform/sanitizer");

async function migrateDatabase() {
  try {
    await connectDB();
    console.log("=== DB Migration: Retroactive Sanitization Started ===");

    // Fetch all medicines
    const medicines = await Medicine.find({});
    console.log(`Found ${medicines.length} medicines in the database.`);

    let updatedCount = 0;
    let failedCount = 0;

    for (const doc of medicines) {
      try {
        let hasChanges = false;
        
        // 1. Clean Name
        const newName = cleanMedicineName(doc.name);
        if (newName && newName !== doc.name) {
          doc.name = newName;
          hasChanges = true;
        }

        // 2. Clean Description
        const newDesc = cleanDescription(doc.description);
        if (newDesc && newDesc !== doc.description) {
          doc.description = newDesc;
          hasChanges = true;
        }

        // 3. Clean Side Effects
        const newSideEffects = cleanSideEffects(doc.side_effects);
        // Deep compare array
        if (JSON.stringify(newSideEffects) !== JSON.stringify(doc.side_effects)) {
          doc.side_effects = newSideEffects;
          hasChanges = true;
        }

        // 4. Clean FAQ
        if (doc.faq && doc.faq.length > 0) {
          const newFaq = cleanFaq(doc.faq);
          // Simplified deep compare for FAQ array of objects
          if (JSON.stringify(newFaq) !== JSON.stringify(doc.faq)) {
            doc.faq = newFaq;
            hasChanges = true;
          }
        }

        if (hasChanges) {
          await doc.save();
          updatedCount++;
          if (updatedCount % 50 === 0) {
            console.log(`...Updated ${updatedCount} records`);
          }
        }
      } catch (err) {
        failedCount++;
        console.error(`Failed to update DB record for ID ${doc._id}: ${err.message}`);
      }
    }

    console.log("=== DB Migration Completed ===");
    console.log(`Total Records:   ${medicines.length}`);
    console.log(`Updated Records: ${updatedCount}`);
    console.log(`Failed Records:  ${failedCount}`);

  } catch (err) {
    console.error("Migration failed due to error:", err);
  } finally {
    mongoose.disconnect();
  }
}

migrateDatabase();
