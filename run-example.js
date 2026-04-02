/**
 * Example Executor to test the ETL Pipeline.
 */
require("dotenv").config();
const { runETL } = require("./etl/pipeline");
const { searchMedicines } = require("./etl/search");
const mongoose = require("mongoose");

const dummyRawData = [
  // Two entries that should COLLAPSE into the exact same canonical medicine.
  {
    name: "Crocin 650 Tablet",
    salt: "Paracetamol (650mg)",
    price: 30,
    platform: "1mg",
    url: "https://1mg.com/crocin"
  },
  {
    name: "Crocin 650",         // Note: Missing 'Tablet'
    salt: "Paracetamol 650 mg", // Note: Spaced differently
    price: 28,                  // A different price
    platform: "pharmeasy",
    url: "https://pharmeasy.in/crocin"
  },
  
  // A completely separate medicine but with the exact same SALT base
  {
    name: "Calpol 650mg Strip of 15 Tablets",
    salt: "Paracetamol (650mg)",
    price: 32,
    platform: "netmeds",
    url: "https://netmeds.com/calpol"
  },
  
  // A junk entry to test filtering
  {
    name: "",
    salt: null,
    price: "NaN",
    platform: "unknown",
    url: "invalid"
  }
];

async function main() {
  console.log("---- RUNNING ETL PIPELINE ----\n");
  await runETL(dummyRawData);

  console.log("\n---- TESTING SEARCH FUNCTION ----\n");
  // Our search query: Let's search for "crocin"
  console.log("Searching for: 'Crocin 650'...");
  const results = await searchMedicines("Crocin 650");
  
  console.log(JSON.stringify(results, null, 2));

  // Disconnect so CLI exits
  setTimeout(()=> {
      mongoose.disconnect();
      process.exit(0);
  }, 1000);
}

main();
