/**
 * remap-topics.js
 * Maps every old DB topic name → new taxonomy topic name
 * and bulk-updates the questions table in Supabase.
 *
 * Run: node remap-topics.js
 */

import { createClient } from "@supabase/supabase-js";

const supabase = createClient(
  "https://nunbpwaxqqgfxrosqfhw.supabase.co",
  "sb_publishable_nAnAiYcJ5uAwiqyytR41iw_Q6U5KMZF"
);

// ─── Full mapping: old topic name → new taxonomy topic name ──────────────────
// Topics with the SAME name in old and new taxonomy are NOT listed here (no update needed).
// Only changed/merged/renamed ones are listed.

const TOPIC_MAP = {
  // ── Domain 1.A — Rajasthan History & Culture ────────────────────────────
  "Chalcolithic & Copper Age Cultures":           "Pre-historical sites — Palaeolithic to Chalcolithic & Bronze Age",
  "Palaeolithic & Mesolithic Sites":              "Pre-historical sites — Palaeolithic to Chalcolithic & Bronze Age",
  "Sources of Rajasthan History":                 "Sources of History — Archaeological, Archival, Literary, Numismatic",

  "Cooperation & Resistance with Central Power":  "Cooperation & Resistance with Central Power (Delhi Sultanate & Mughals)",
  "Political & Social Conditions 18th-19th Century": "Political & Social Conditions — 18th & 19th Century",

  "Newspapers & Journalism in Rajasthan":         "Newspapers & Journalism",

  "Monuments & Memorials":                        "Monuments, Memorials & Man-made Water Bodies",
  "Man-made Water Bodies (Baoris & Stepwells)":   "Monuments, Memorials & Man-made Water Bodies",

  "Music & Musical Texts":                        "Music, Musical Texts & Instruments",

  "Costumes & Attires":                           "Costumes, Attires, Jewellery & Ornaments",
  "Jewellery & Ornaments":                        "Costumes, Attires, Jewellery & Ornaments",

  "Folk Artists & Personalities":                 "Artists, Literary Figures & Folk Personalities",
  "Artists & Literary Figures":                   "Artists, Literary Figures & Folk Personalities",

  "Sports Personalities":                         "Sports & Distinguished Personalities",
  "Distinguished Personalities":                  "Sports & Distinguished Personalities",

  // ── Domain 1.B — Ancient & Medieval India ──────────────────────────────
  "Post-Mauryan Dynasties (Kushan, Satavahan, Gupta)": "Post-Mauryan Dynasties — Kushan, Satavahana, Gupta",
  "South Indian Dynasties (Chalukya, Pallava, Chola)": "South Indian Dynasties — Chalukya, Pallava, Chola",
  "Literature & Sanskrit Texts":                  "Literature, Sanskrit Texts & Scientific Development",
  "Scientific Development — Ancient India":        "Literature, Sanskrit Texts & Scientific Development",
  "Inscriptions & Edicts":                        "Inscriptions, Edicts & Sites",
  "Sites & Cities — Ancient India":               "Inscriptions, Edicts & Sites",
  "Indian Knowledge & Value System (Varna, Purushartha, Sanskara)": "Indian Knowledge & Value System — Varna, Purushartha, Sanskara",
  "Vijayanagar Empire":                           "Vijayanagara Empire",

  // ── Domain 1.C — Modern India ──────────────────────────────────────────
  "Books & Authors — Modern India":               "Newspapers, Press, Books & Authors — Modern India",
  "Newspapers & Press — Modern India":            "Newspapers, Press, Books & Authors — Modern India",
  "British Imperialism & Resistance (Maratha, Mysore, Sikh)": "British Imperialism & Resistance — Maratha, Mysore, Sikh",
  "Committees & Commissions":                     "Committees, Commissions & Institutions",
  "Institutions & Organisations — Modern India":  "Committees, Commissions & Institutions",
  "Planning & Economic Reforms":                  "Planning, Economic Reforms & LPG",
  "LPG Reforms":                                  "LPG Reforms & Foreign Direct Investment",
  "Post-Independence India":                      "Nation Building & State Reorganisation",

  // ── Domain 2.A — Rajasthan Geography ───────────────────────────────────
  "Demographics & Census":                        "Demographics, Census & Population",
  "Population — Growth, Density, Literacy, Sex Ratio": "Demographics, Census & Population",
  "Urbanization":                                 "Urbanisation",

  // ── Domain 2.B — India Geography ───────────────────────────────────────
  "Himalayan Geography":                          "Himalayan Geography & Mountain Passes",
  "Mountain Passes":                              "Himalayan Geography & Mountain Passes",
  "Hills & Ghats of India":                       "Hills, Ghats & Plains",
  "Lakes of India":                               "Lakes, Ports & Coastline",
  "Ports & Coastline of India":                   "Lakes, Ports & Coastline",
  "Agriculture — Important Crops":                "Agriculture — Important Crops & Irrigation",
  "Irrigation — India":                           "Agriculture — Important Crops & Irrigation",
  "Shifting Agriculture & Practices":             "Agriculture — Important Crops & Irrigation",
  "Industries & Industrial Regions of India":     "Industries & Industrial Regions",

  // ── Domain 2.C — World Geography ───────────────────────────────────────
  "Major Rivers of the World":                    "Major Rivers & Lakes of the World",
  "Major Lakes of the World":                     "Major Rivers & Lakes of the World",
  "Natural Vegetation — World":                   "Natural Vegetation & Forest Zones",
  "Deforestation & Forest Zones":                 "Desertification, Deforestation & Land Use",
  "Desertification & Land Use":                   "Desertification, Deforestation & Land Use",
  "Deserts & Water Bodies":                       "Mountains, Plateaus, Plains & Deserts",
  "Transport Network — World":                    "Transport Network & Renewable Energy — World",
  "Renewable Energy — World":                     "Transport Network & Renewable Energy — World",

  // ── Domain 3.A — Rajasthan Economy ─────────────────────────────────────
  "GSDP & Five Year Plans":                       "GSDP & Economic Overview",
  "Price Indices — Rajasthan":                    "Price Indices & FRBM",
  "Rajasthan FRBM":                               "Price Indices & FRBM",

  // ── Domain 3.B — Indian Economy ────────────────────────────────────────
  "Employment & Labour":                          "Employment, Labour & Skill Development",
  "Skill Development & Employment":               "Employment, Labour & Skill Development",
  "Foreign Direct Investment":                    "LPG Reforms & Foreign Direct Investment",
  "Ease of Doing Business":                       "Industrial Policy & Sectors",
  "Public Goods & Market Failure":                "Social Justice, Empowerment & Market Regulation",
  "Competition & Market Regulation":              "Social Justice, Empowerment & Market Regulation",
  "Social Justice & Empowerment":                 "Social Justice, Empowerment & Market Regulation",
  "Resource Mobilization":                        "Public Finance & Taxation",
  "Five Year Plans & Planning":                   "Five Year Plans & Planning Commission",
  "Energy, Transportation & Communication — India": "Energy, Transport & Communication — India",

  // ── Domain 4.A — Rajasthan Polity ──────────────────────────────────────
  "Rajasthan Council of Ministers":               "Chief Minister & Council of Ministers",
  "Chief Minister of Rajasthan":                  "Chief Minister & Council of Ministers",
  "District Collector & District Magistrate":     "District Collector & District Administration",
  "Superintendent of Police":                     "Sub-district Administration — SDO, Tehsildar, SP",
  "Sub-Divisional Officer & Tehsildar":           "Sub-district Administration — SDO, Tehsildar, SP",
  "Subordinate Courts":                           "Subordinate Courts & Board of Revenue",
  "Board of Revenue":                             "Subordinate Courts & Board of Revenue",
  "Lokayukta — Rajasthan":                        "Lokayukta",
  "Appointments to Constitutional Bodies":        "Appointments to Constitutional Bodies",
  "Social Audit & Grievance Redressal":           "Social Audit & Grievance Redressal",

  // ── Domain 4.B — Indian Polity ─────────────────────────────────────────
  "Panchayati Raj — Constitutional":              "Panchayati Raj — Constitutional Provisions",
  "Panchayati Raj — Rajasthan (Constitutional Provisions)": "Panchayati Raj — Rajasthan",
  "Urban Local Bodies & Municipalities (Constitutional Provisions)": "Urban Local Bodies — Constitutional Provisions",
  "Parliamentary Procedures & Rules":             "Parliamentary Procedures, Rules & Committees",
  "Parliamentary Committees":                     "Parliamentary Procedures, Rules & Committees",
  "Supreme Court — Composition & Procedure":      "Supreme Court — Composition, Procedure & Landmark Cases",
  "Supreme Court Landmark Cases":                 "Supreme Court — Composition, Procedure & Landmark Cases",
  "Central Vigilance Commission":                 "Central Vigilance Commission & Lokpal",
  "Central Vigilance Commission & Anti-Corruption Bodies": "Central Vigilance Commission & Lokpal",
  "Lokpal & Anti-Corruption Bodies":              "Central Vigilance Commission & Lokpal",
  "National Human Rights Commission":             "NHRC, NCW & NCPCR",
  "National Commission for Women":                "NHRC, NCW & NCPCR",
  "National Commission for Child Rights":         "NHRC, NCW & NCPCR",
  "Inter-State Council & Federalism":             "Inter-State Council",

  // ── Domain 5.A — Science & Technology ──────────────────────────────────
  "Human Anatomy & Physiology":                   "Human Anatomy & Physiology",   // same ✓ (listed for clarity)
  "Biotechnology & Genetic Engineering":          "Genetics, Heredity & Biotechnology",
  "Genetics & Heredity":                          "Genetics, Heredity & Biotechnology",
  "Diseases — Vector, Water & Food Borne":        "Diseases — Vector, Water, Food-borne & Public Health",
  "Public Health Programmes":                     "Diseases — Vector, Water, Food-borne & Public Health",
  "Blood Groups & Immunology":                    "Blood Groups, Immunology & Medicines",
  "Medicines & Drugs":                            "Blood Groups, Immunology & Medicines",
  "Agriculture & Animal Husbandry — Science":     "Agriculture, Animal Husbandry, Horticulture & Forestry",
  "Horticulture & Forestry":                      "Agriculture, Animal Husbandry, Horticulture & Forestry",
  "Pollution — Air, Water, Soil":                 "Pollution — Air, Water & Soil",
  "Composting & Waste Management":               "Pollution — Air, Water & Soil",
  "Biodiversity & Hotspots":                      "Biodiversity, Hotspots & Wildlife",
  "Wildlife & Protected Areas":                   "Biodiversity, Hotspots & Wildlife",
  "Natural Resources & Conservation":             "Natural Resources, Sustainable Development & SDGs",
  "Sustainable Development Goals":                "Natural Resources, Sustainable Development & SDGs",
  "Bioindicators & Lichen":                       "Ecosystem & Ecological Concepts",
  "Environmental Impact Assessment":              "Ecosystem & Ecological Concepts",
  "Physics — Basic Concepts":                     "Physics — Basic Concepts & Optics",
  "Optics & Human Eye":                           "Physics — Basic Concepts & Optics",
  "ISRO Satellites & Missions":                   "ISRO — Satellites, Missions, Centres & Facilities",
  "ISRO Centres & Facilities":                    "ISRO — Satellites, Missions, Centres & Facilities",
  "Defence Equipment & Weapons":                  "Defence Equipment, Weapons & DRDO",
  "DRDO & Defence Research":                      "Defence Equipment, Weapons & DRDO",
  "Key S&T Programmes — India & Rajasthan":       "Indigenisation & Key S&T Programmes",
  "Indigenisation of Science & Technology":       "Indigenisation & Key S&T Programmes",

  // ── Domain 6 — Schemes ──────────────────────────────────────────────────
  "Health Schemes (Chiranjeevi, etc.)":           "Health Schemes — Chiranjeevi etc.",
  "Women & Child Welfare Schemes":                "Women, Child, SC/ST & Minority Welfare Schemes",
  "SC/ST & Minority Welfare Schemes":             "Women, Child, SC/ST & Minority Welfare Schemes",
  "Social Welfare Schemes":                       "Women, Child, SC/ST & Minority Welfare Schemes",
  "Employment & Livelihood Schemes":              "Employment, Livelihood & MSME Schemes",
  "MSME & Entrepreneurship Schemes":              "Employment, Livelihood & MSME Schemes",
  "Digital & E-Governance Schemes":               "Digital, E-Governance & Education Schemes",
  "Education & Scholarship Schemes":              "Digital, E-Governance & Education Schemes",
  "Health Schemes (Ayushman Bharat, etc.)":       "Health Schemes — Ayushman Bharat etc.",
  "Women & Child Schemes (Beti Bachao, etc.)":    "Women & Child Schemes — Beti Bachao etc.",
  "SC/ST & Minority Schemes":                     "SC/ST, Minority, Disability & Senior Citizen Schemes",
  "Agriculture Schemes (PM Kisan, KUSUM, etc.)":  "Agriculture Schemes — PM Kisan, KUSUM etc.",
  "Employment Schemes (MNREGA, etc.)":            "Employment & Financial Inclusion — MNREGA, Jan Dhan, Mudra",
  "Financial Inclusion (Jan Dhan, Mudra, etc.)":  "Employment & Financial Inclusion — MNREGA, Jan Dhan, Mudra",
  "Economic Schemes":                             "Employment & Financial Inclusion — MNREGA, Jan Dhan, Mudra",
  "Housing Schemes (PM Awas, etc.)":              "Housing, Urban Development & Skill Development Schemes",
  "Urban Development (AMRUT, Smart Cities, etc.)": "Housing, Urban Development & Skill Development Schemes",
  "Skill Development Schemes":                    "Housing, Urban Development & Skill Development Schemes",
  "Energy Schemes (Ujjwala, Solar Mission, etc.)": "Digital India, Energy & Technology Schemes",
  "Digital India & Technology Schemes":           "Digital India, Energy & Technology Schemes",
  "National Schemes & Programmes":               "Employment & Financial Inclusion — MNREGA, Jan Dhan, Mudra",

  // ── Domain 7 — Reasoning ────────────────────────────────────────────────
  "Assertion & Reason":                           "Analytical & Critical Reasoning",
  "Averages — Arithmetic, Geometric & Harmonic":  "Averages — AM, GM, HM",
  "Work, Speed & Time":                           "Speed, Distance & Time",
};

// ─── Valid new taxonomy topic names (to validate mappings) ───────────────────
// Pulled directly from taxonomyData.js
const VALID_TOPICS = new Set([
  "Pre-historical sites — Palaeolithic to Chalcolithic & Bronze Age",
  "Ancient Rajasthan Society & Culture",
  "Sources of History — Archaeological, Archival, Literary, Numismatic",
  "Medieval Dynasties & Kingdoms", "Prominent Rulers & Achievements",
  "Medieval Battles & Events", "Cooperation & Resistance with Central Power (Delhi Sultanate & Mughals)",
  "Medieval Administration & Governance", "Revenue System",
  "Political & Social Conditions — 18th & 19th Century",
  "Peasant & Tribal Movements", "Praja Mandal & Freedom Struggle",
  "Social Reform Movements", "Newspapers & Journalism",
  "Historical Institutions & Organisations", "Mass Awakening Movements",
  "Integration of Rajasthan", "Post-Merger Political History",
  "Temple Architecture", "Forts & Palaces", "Havelis & Architecture",
  "Monuments, Memorials & Man-made Water Bodies",
  "Painting Schools of Rajasthan", "Handicrafts & Crafts",
  "Rajasthani Language & Dialects", "Rajasthani Literature & Folk Literature",
  "Folk Drama & Performing Arts", "Music, Musical Texts & Instruments",
  "Classical Music & Dance",
  "Folk Dance", "Folk Music & Instruments", "Fairs & Festivals",
  "Social Customs & Traditions", "Costumes, Attires, Jewellery & Ornaments",
  "Religious Practices", "Folk Deities & Religious Traditions",
  "Saints, Sects & Religious Communities", "Temples & Religious Sites",
  "Freedom Fighters & Reformers", "Artists, Literary Figures & Folk Personalities",
  "Sports & Distinguished Personalities",
  "Indus Valley & Harappan Civilisation", "Vedic Civilisation & Society",
  "Religious Sects — Buddhism, Jainism & Ajivakas",
  "Indian Knowledge & Value System — Varna, Purushartha, Sanskara",
  "Philosophical & Religious Sects",
  "Mauryan Empire & Administration", "Post-Mauryan Dynasties — Kushan, Satavahana, Gupta",
  "South Indian Dynasties — Chalukya, Pallava, Chola",
  "Art & Architecture — Ancient India", "Literature, Sanskrit Texts & Scientific Development",
  "Trade, Economy & Guilds", "Inscriptions, Edicts & Sites",
  "Delhi Sultanate", "Mughal Empire & Administration",
  "Vijayanagara Empire", "Maratha Empire", "Regional Kingdoms & Dynasties",
  "Rajput Kingdoms & Chahamanas", "Bhakti & Sufi Movements",
  "Medieval Art, Architecture & Literature",
  "British Imperialism & Resistance — Maratha, Mysore, Sikh", "Revolt of 1857",
  "British Political, Economic & Administrative Policies",
  "Constitutional & Legislative Acts", "Emergence of Nationalism",
  "Social & Religious Reform Movements",
  "Newspapers, Press, Books & Authors — Modern India",
  "Chronology of Modern India Events",
  "National Movement & INC", "Revolutionary Movements",
  "Civil Disobedience & Quit India", "Regional Movements",
  "Committees, Commissions & Institutions", "Partition of India",
  "Nation Building & State Reorganisation", "Institutional Building — Nehruvian Era",
  "Planning, Economic Reforms & LPG", "Art & Culture — Modern India",
  "Location, Extent & Physiography", "Aravalli Range & Mountain Peaks",
  "Thar Desert & Arid Geography", "Plains & Other Landforms",
  "Rivers of Rajasthan", "Lakes & Water Bodies",
  "Groundwater & Water Conservation", "Irrigation Projects of Rajasthan",
  "IGNP — Indira Gandhi Nahar Project",
  "Climate of Rajasthan", "Soils of Rajasthan",
  "Forest & Natural Vegetation", "Wildlife Sanctuaries & National Parks",
  "Biodiversity & Conservation", "Forest Policy & Environment",
  "Minerals — Metallic & Non-Metallic", "Agriculture in Rajasthan",
  "Livestock & Animal Husbandry", "Agricultural Research Centres",
  "Tourism Geography", "Demographics, Census & Population",
  "Tribes of Rajasthan", "Districts & Administrative Geography",
  "State Symbols of Rajasthan", "Urbanisation",
  "Physiographic Divisions of India", "Himalayan Geography & Mountain Passes",
  "Hills, Ghats & Plains", "River Systems of India",
  "Lakes, Ports & Coastline", "Monsoon & Climate — India",
  "Population & Census Data", "Agriculture — Important Crops & Irrigation",
  "Industries & Industrial Regions", "National Highways & Corridors",
  "Oil Refineries & Energy Infrastructure",
  "Mountains, Plateaus, Plains & Deserts", "Major Rivers & Lakes of the World",
  "Straits & Water Bodies", "Agricultural Types & Climatic Zones",
  "Natural Vegetation & Forest Zones", "Desertification, Deforestation & Land Use",
  "Ozone Layer Depletion", "Major Industrial Regions of the World",
  "Transport Network & Renewable Energy — World",
  "Rajasthan Budget & Finance", "GSDP & Economic Overview",
  "Price Indices & FRBM", "State Finance Commission", "Five Year Plans — Rajasthan",
  "Agriculture — Crops & Production", "Animal Husbandry & Dairy",
  "Rural Development Programmes", "Panchayati Raj & Rural Economy",
  "Industries & RIICO", "Investment & Industry Policy",
  "Mineral Policy & Industries", "MSMEs in Rajasthan",
  "Service Sector in Rajasthan", "Energy Sector — Solar & Renewable",
  "Infrastructure & Transport", "Thermal Power Projects",
  "Communication & IT Infrastructure", "Education & Skill Development",
  "Health & Medical Infrastructure",
  "National Income & GDP", "Inflation & Price Indices — CPI/WPI",
  "Human Development Index & Reports", "Poverty Measurement & Indices",
  "Employment, Labour & Skill Development",
  "Sustainable Development & Environmental Degradation",
  "Economic Growth & Development",
  "Monetary Policy & RBI", "Public Finance & Taxation", "GST & Indirect Taxes",
  "Money Market & Banking", "Fiscal Policy & Budget",
  "Fiscal Federalism & Finance Commission", "Financial Sector Reforms",
  "Agricultural Economics & MSP", "Industrial Policy & Sectors",
  "LPG Reforms & Foreign Direct Investment", "Balance of Trade & External Sector",
  "Role of Service Sector", "Social Justice, Empowerment & Market Regulation",
  "Five Year Plans & Planning Commission", "Energy, Transport & Communication — India",
  "Rajasthan Legislative Assembly", "Governor of Rajasthan",
  "Chief Minister & Council of Ministers", "President's Rule in Rajasthan",
  "Administrative History & Divisions", "District Collector & District Administration",
  "Sub-district Administration — SDO, Tehsildar, SP", "RPSC & Public Services",
  "Chief Secretary & State Secretariat", "Divisional Commissioner",
  "Rajasthan High Court", "Subordinate Courts & Board of Revenue",
  "Advocate General", "State Election Commission",
  "Rajasthan Information Commission", "State Human Rights Commission",
  "Lokayukta", "Rajasthan State Women Commission",
  "Panchayati Raj — Rajasthan", "Urban Local Bodies & Municipalities",
  "Jan Aadhar & E-Governance", "Right to Public Services Act",
  "Rajasthan Public Examination Act 2022", "Social Audit & Grievance Redressal",
  "Appointments to Constitutional Bodies", "Ajmer Chief Controller of Examination",
  "Framing of Constitution & Preamble", "Citizenship", "Fundamental Rights",
  "Directive Principles of State Policy", "Fundamental Duties",
  "Constitutional Amendments", "Schedules of the Constitution",
  "Emergency Provisions", "Federalism & Centre-State Relations",
  "PESA Act & Scheduled Areas", "Panchayati Raj — Constitutional Provisions",
  "Urban Local Bodies — Constitutional Provisions",
  "President of India", "Vice President & Council of Ministers",
  "Lok Sabha & Rajya Sabha", "Parliamentary Procedures, Rules & Committees",
  "State Legislatures & Bicameralism",
  "Supreme Court — Composition, Procedure & Landmark Cases",
  "PIL & Judicial Activism", "High Courts & Subordinate Courts",
  "Election Commission of India", "CAG & Audit",
  "Central Information Commission & RTI", "Central Vigilance Commission & Lokpal",
  "Finance Commission", "UPSC & Public Service Commissions", "NITI Aayog",
  "NHRC, NCW & NCPCR", "Inter-State Council",
  "Citizens Charter & Service Delivery", "Public Policy",
  "Human Anatomy & Physiology", "Genetics, Heredity & Biotechnology",
  "Diseases — Vector, Water, Food-borne & Public Health",
  "Blood Groups, Immunology & Medicines", "Biochemistry — Enzymes & Nutrition",
  "General Biology & Everyday Science",
  "Agriculture, Animal Husbandry, Horticulture & Forestry",
  "Pollution — Air, Water & Soil", "Climate Change & Global Warming",
  "Biodiversity, Hotspots & Wildlife", "Ecosystem & Ecological Concepts",
  "Natural Resources, Sustainable Development & SDGs",
  "International Environmental Programmes", "Biofuels & Renewable Energy",
  "Chemistry — Basic Concepts & Compounds", "Organic Chemistry & Fibres",
  "Food Adulteration & Safety", "Physics — Basic Concepts & Optics",
  "Nanotechnology", "Energy — Solar & Renewable",
  "Computer & Information Technology", "Communication Technology",
  "Artificial Intelligence & Machine Learning",
  "ISRO — Satellites, Missions, Centres & Facilities",
  "Defence Equipment, Weapons & DRDO", "Indigenisation & Key S&T Programmes",
  "Contribution of Indians in Science & Technology",
  "Health Schemes — Chiranjeevi etc.", "Women, Child, SC/ST & Minority Welfare Schemes",
  "Disability & Senior Citizen Schemes", "Agriculture & Farmer Schemes",
  "Employment, Livelihood & MSME Schemes", "Housing & Urban Development Schemes",
  "Digital, E-Governance & Education Schemes", "Energy & Environment Schemes",
  "Health Schemes — Ayushman Bharat etc.", "Women & Child Schemes — Beti Bachao etc.",
  "SC/ST, Minority, Disability & Senior Citizen Schemes",
  "Agriculture Schemes — PM Kisan, KUSUM etc.",
  "Employment & Financial Inclusion — MNREGA, Jan Dhan, Mudra",
  "Housing, Urban Development & Skill Development Schemes",
  "Digital India, Energy & Technology Schemes",
  "Statement & Assumptions", "Statement & Arguments", "Statement & Conclusions",
  "Statement & Courses of Action", "Syllogism", "Analytical & Critical Reasoning",
  "Coding-Decoding", "Blood Relations", "Direction & Distance",
  "Seating & Ranking Arrangement", "Letter & Alphabet Series", "Number Series",
  "Analogy & Classification", "Mirror & Water Images", "Venn Diagrams",
  "Figure, Pattern & Shapes",
  "Number System", "Number Series & Patterns", "Surds & Powers",
  "Inequality & Number Relations", "Percentage & Profit-Loss",
  "Simple & Compound Interest", "Ratio, Proportion & Partnership",
  "Time & Work", "Speed, Distance & Time", "Age Problems",
  "Geometry & Mensuration", "Permutations & Combinations", "Probability",
  "Averages — AM, GM, HM", "Statistics — Mean, Median & Mode", "Counting Figures",
  "Data Interpretation — Tables", "Data Interpretation — Bar & Line Graphs",
  "Data Interpretation — Pie Charts",
  "Rajasthan Politics & Governance", "Rajasthan Economy & Development",
  "Rajasthan Sports & Awards", "Rajasthan Schemes & Programmes",
  "Rajasthan Art, Culture & Heritage", "Rajasthan Science & Technology",
  "Rajasthan Public Examination Act 2022",
  "National Politics & Governance", "National Economy & Finance",
  "National Sports & Awards", "National Science & Technology",
  "Books, Awards & Personalities", "Days & Celebrations",
  "Bills & Legislation", "Contemporary Issues & Events",
  "International Organisations & Reports", "Defence & Geopolitics",
  "World Economy & Trade", "Global Sports & Awards",
  "International Politics & Elections",
]);

async function run() {
  console.log("Fetching all questions...");
  const { data: questions, error } = await supabase
    .from("questions")
    .select("id, topic")
    .not("topic", "is", null);

  if (error) { console.error("Fetch error:", error); return; }
  console.log(`Fetched ${questions.length} questions`);

  let updated = 0, skipped = 0, alreadyValid = 0, unknown = [];

  for (const q of questions) {
    const oldTopic = q.topic;

    // Already a valid new taxonomy topic — no change needed
    if (VALID_TOPICS.has(oldTopic)) {
      alreadyValid++;
      continue;
    }

    const newTopic = TOPIC_MAP[oldTopic];

    if (!newTopic) {
      unknown.push({ id: q.id, topic: oldTopic });
      skipped++;
      continue;
    }

    const { error: updateErr } = await supabase
      .from("questions")
      .update({ topic: newTopic })
      .eq("id", q.id);

    if (updateErr) {
      console.error(`  ERROR id=${q.id}: ${updateErr.message}`);
    } else {
      updated++;
      process.stdout.write(`\r  Updated: ${updated}`);
    }
  }

  console.log(`\n\n✓ Done`);
  console.log(`  Already valid (no change):  ${alreadyValid}`);
  console.log(`  Updated to new topic name:  ${updated}`);
  console.log(`  Skipped (unmapped):         ${skipped}`);

  if (unknown.length > 0) {
    console.log(`\n⚠ Unmapped topics (need manual review):`);
    const groups = {};
    for (const u of unknown) {
      groups[u.topic] = (groups[u.topic] || 0) + 1;
    }
    Object.entries(groups)
      .sort((a, b) => b[1] - a[1])
      .forEach(([t, n]) => console.log(`  [${n}q] ${t}`));
  }
}

run().catch(console.error);
