require("dotenv").config();
const mongoose = require("mongoose");
const connectDB = require("./etl/load/db");
const { Medicine, Price } = require("./etl/load/models");

async function resetDB() {
  try {
    await connectDB();

    const medResult = await Medicine.deleteMany({});

    const priceResult = await Price.deleteMany({});

  } catch {
  } finally {
    mongoose.disconnect();
  }
}

resetDB();
