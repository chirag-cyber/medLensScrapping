const DOSAGE_REGEX = /((?:\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)?\s*(?:\+|and|&|\/|-)?\s*)*\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%|-?gm?|-?l|-?ml|-?mcg|-?mg))(?!\w)/i;

function extractDosage(str, isRisky = false) {
  if (!str) return null;

  if (String(str).match(/^https?:\/\//i) || String(str).match(/^\/[a-z0-9-]+\//i)) {
    return null;
  }

  let cleanStr = String(str);
  
  cleanStr = cleanStr.replace(/\|\s*1mg\b/gi, " ");
  cleanStr = cleanStr.replace(/-\s*1mg\b/gi, " ");
  cleanStr = cleanStr.replace(/\b1mg\s+platform\b/gi, " ");
  cleanStr = cleanStr.replace(/1mg\.com/gi, " ");

  const matches = [...cleanStr.matchAll(new RegExp(DOSAGE_REGEX.source, "gi"))]
    .map((match) => {
      let m = match[1].toLowerCase();
      const parts = [...m.matchAll(/(\d+(?:\.\d+)?)\s*(mg|ml|mcg|g|gm|kg|%)?/gi)];
      
      if (parts.length === 1) {
          return m.replace(/\s+/g, "");
      }
      
      let finalParts = [];
      let lastUnit = "mg"; 
      for (let i = parts.length - 1; i >= 0; i--) {
         if (parts[i][2]) {
             lastUnit = parts[i][2];
         }
         finalParts.unshift(parts[i][1] + lastUnit);
      }
      return finalParts.join("+");
    })
    .filter(Boolean);

  if (matches.length === 0) return null;

  const validMatches = matches.filter((m) => m !== "1mg");

  if (validMatches.length === 0) return null;

  validMatches.sort((a, b) => {
      const aPlus = (a.match(/\+/g) || []).length;
      const bPlus = (b.match(/\+/g) || []).length;
      if (aPlus !== bPlus) return bPlus - aPlus;
      
      const aMag = parseFloat(a) || 0;
      const bMag = parseFloat(b) || 0;
      return bMag - aMag;
  });

  const extracted = validMatches[0];
  
  if (extracted === "1mg") return null;

  return extracted;
}

console.log("Dolo 650 Tablet | 1mg ->", extractDosage("Dolo 650 Tablet | 1mg"));
console.log("Glyxambi 25 mg 5 mg Tablet ->", extractDosage("Glyxambi 25 mg 5 mg Tablet"));
console.log("Crocin Tablet | 1mg ->", extractDosage("Crocin Tablet | 1mg"));
console.log("url /drugs/crocin-1mg ->", extractDosage("/drugs/crocin-1mg"));
console.log("Dolo 650mg Tablet | 1mg ->", extractDosage("Dolo 650mg Tablet | 1mg"));
console.log("AIRZ CAPSULE | 1mg ->", extractDosage("AIRZ CAPSULE | 1mg"));
console.log("AIRZ CAPSULE 500mg ->", extractDosage("AIRZ CAPSULE 500mg"));
