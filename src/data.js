export const STOCKS = [
  { symbol: 'TSLA', name: 'Tesla, Inc.', sector: 'Automotive', exchange: 'NASDAQ', focus: 'EV & Autonomy', base: 438.72, cap: 1_424_000_000_000, volume: 98200000, pe: 186.2, dividend: 0, beta: 2.31 },
  { symbol: 'NVDA', name: 'NVIDIA Corporation', sector: 'Semiconductors', exchange: 'NASDAQ', focus: 'AI & Compute', base: 188.37, cap: 4_580_000_000_000, volume: 168200000, pe: 51.8, dividend: 0.02, beta: 2.04 },
  { symbol: 'AMD', name: 'Advanced Micro Devices', sector: 'Semiconductors', exchange: 'NASDAQ', focus: 'AI & Compute', base: 194.22, cap: 316_000_000_000, volume: 46300000, pe: 97.1, dividend: 0, beta: 1.72 },
  { symbol: 'AAPL', name: 'Apple Inc.', sector: 'Consumer Tech', exchange: 'NASDAQ', focus: 'Mega cap', base: 246.81, cap: 3_680_000_000_000, volume: 43600000, pe: 32.3, dividend: 0.42, beta: 1.18 },
  { symbol: 'MSFT', name: 'Microsoft Corporation', sector: 'Software', exchange: 'NASDAQ', focus: 'AI & Compute', base: 522.13, cap: 3_880_000_000_000, volume: 19200000, pe: 37.4, dividend: 0.67, beta: 0.98 },
  { symbol: 'GOOGL', name: 'Alphabet Inc.', sector: 'Internet', exchange: 'NASDAQ', focus: 'Mega cap', base: 211.64, cap: 2_580_000_000_000, volume: 19250000, pe: 23.4, dividend: 0.45, beta: 1.02 },
  { symbol: 'META', name: 'Meta Platforms', sector: 'Internet', exchange: 'NASDAQ', focus: 'AI & Compute', base: 718.31, cap: 1_820_000_000_000, volume: 17100000, pe: 27.8, dividend: 0.31, beta: 1.27 },
  { symbol: 'PLTR', name: 'Palantir Technologies', sector: 'Software', exchange: 'NASDAQ', focus: 'AI & Compute', base: 168.42, cap: 396_000_000_000, volume: 64100000, pe: 264.1, dividend: 0, beta: 2.78 },
  { symbol: 'AVGO', name: 'Broadcom Inc.', sector: 'Semiconductors', exchange: 'NASDAQ', focus: 'AI & Compute', base: 361.28, cap: 1_710_000_000_000, volume: 19200000, pe: 102.1, dividend: 0.74, beta: 1.28 },
  { symbol: 'TSM', name: 'Taiwan Semiconductor', sector: 'Semiconductors', exchange: 'NYSE', focus: 'AI & Compute', base: 241.19, cap: 1_250_000_000_000, volume: 11800000, pe: 28.9, dividend: 0.88, beta: 1.16 },
  { symbol: 'ASML', name: 'ASML Holding', sector: 'Semiconductors', exchange: 'NASDAQ', focus: 'AI & Compute', base: 918.56, cap: 361_000_000_000, volume: 864000, pe: 35.2, dividend: 0.95, beta: 1.42 },
  { symbol: 'ARM', name: 'Arm Holdings', sector: 'Semiconductors', exchange: 'NASDAQ', focus: 'AI & Compute', base: 158.93, cap: 168_000_000_000, volume: 5210000, pe: 152.5, dividend: 0, beta: 3.06 },
  { symbol: 'ORCL', name: 'Oracle Corporation', sector: 'Software', exchange: 'NYSE', focus: 'Cloud', base: 274.09, cap: 781_000_000_000, volume: 10300000, pe: 48.6, dividend: 0.92, beta: 1.14 },
  { symbol: 'QQQ', name: 'Invesco QQQ Trust', sector: 'ETF', exchange: 'NASDAQ', focus: 'Index', base: 585.42, cap: 354_000_000_000, volume: 39200000, pe: 32.7, dividend: 0.51, beta: 1.18 },
  { symbol: 'RIVN', name: 'Rivian Automotive', sector: 'Automotive', exchange: 'NASDAQ', focus: 'EV & Autonomy', base: 16.18, cap: 19_800_000_000, volume: 25900000, pe: null, dividend: 0, beta: 2.07 },
  { symbol: 'LCID', name: 'Lucid Group', sector: 'Automotive', exchange: 'NASDAQ', focus: 'EV & Autonomy', base: 3.82, cap: 11_700_000_000, volume: 23100000, pe: null, dividend: 0, beta: 1.15 },
  { symbol: 'UBER', name: 'Uber Technologies', sector: 'Mobility', exchange: 'NYSE', focus: 'Mobility', base: 91.48, cap: 192_000_000_000, volume: 11400000, pe: 16.9, dividend: 0, beta: 1.35 },
  { symbol: 'RKLB', name: 'Rocket Lab USA', sector: 'Aerospace', exchange: 'NASDAQ', focus: 'Space & Mobility', base: 52.27, cap: 28_400_000_000, volume: 16400000, pe: null, dividend: 0, beta: 2.11 },
  { symbol: 'ASTS', name: 'AST SpaceMobile', sector: 'Aerospace', exchange: 'NASDAQ', focus: 'Space & Mobility', base: 67.81, cap: 18_300_000_000, volume: 14200000, pe: null, dividend: 0, beta: 2.62 },
  { symbol: 'JOBY', name: 'Joby Aviation', sector: 'Aerospace', exchange: 'NYSE', focus: 'Space & Mobility', base: 13.14, cap: 12_600_000_000, volume: 18100000, pe: null, dividend: 0, beta: 2.08 },
  { symbol: 'AMZN', name: 'Amazon.com, Inc.', sector: 'E-commerce', exchange: 'NASDAQ', focus: 'Mega cap', base: 233.74, cap: 2_470_000_000_000, volume: 33100000, pe: 35.1, dividend: 0, beta: 1.34 },
  { symbol: 'NFLX', name: 'Netflix, Inc.', sector: 'Media', exchange: 'NASDAQ', focus: 'Mega cap', base: 1218.45, cap: 516_000_000_000, volume: 2130000, pe: 59.7, dividend: 0, beta: 1.52 },
  { symbol: 'COIN', name: 'Coinbase Global', sector: 'Financials', exchange: 'NASDAQ', focus: 'Crypto', base: 339.12, cap: 86_000_000_000, volume: 9850000, pe: 44.5, dividend: 0, beta: 3.25 },
  { symbol: 'HOOD', name: 'Robinhood Markets', sector: 'Financials', exchange: 'NASDAQ', focus: 'Fintech', base: 117.68, cap: 104_000_000_000, volume: 19800000, pe: 48.1, dividend: 0, beta: 2.91 },
];

export const CRYPTOS = [
  { symbol: 'BTC', name: 'Bitcoin', category: 'Layer 1', base: 118640.32, cap: 2_351_000_000_000, volume: 39_280_000_000, dominance: 58.4, tvl: 0 },
  { symbol: 'ETH', name: 'Ethereum', category: 'Layer 1', base: 4286.78, cap: 516_000_000_000, volume: 19_630_000_000, dominance: 12.8, tvl: 84_700_000_000 },
  { symbol: 'BNB', name: 'BNB', category: 'Exchange', base: 902.14, cap: 124_000_000_000, volume: 2_150_000_000, dominance: 3.1, tvl: 7_800_000_000 },
  { symbol: 'SOL', name: 'Solana', category: 'Layer 1', base: 226.35, cap: 122_000_000_000, volume: 5_450_000_000, dominance: 3.0, tvl: 8_600_000_000 },
  { symbol: 'XRP', name: 'XRP', category: 'Payments', base: 3.14, cap: 185_000_000_000, volume: 3_220_000_000, dominance: 4.6, tvl: 210_000_000 },
  { symbol: 'USDT', name: 'Tether', category: 'Stablecoin', base: 1, cap: 175_000_000_000, volume: 74_300_000_000, dominance: 4.3, tvl: 0 },
  { symbol: 'USDC', name: 'USD Coin', category: 'Stablecoin', base: 1, cap: 68_000_000_000, volume: 8_940_000_000, dominance: 1.7, tvl: 0 },
  { symbol: 'DOGE', name: 'Dogecoin', category: 'Meme', base: 0.2573, cap: 38_700_000_000, volume: 1_720_000_000, dominance: 0.9, tvl: 0 },
  { symbol: 'ADA', name: 'Cardano', category: 'Layer 1', base: 0.914, cap: 32_400_000_000, volume: 596_000_000, dominance: 0.8, tvl: 312_000_000 },
  { symbol: 'TRX', name: 'TRON', category: 'Layer 1', base: 0.3412, cap: 32_100_000_000, volume: 704_000_000, dominance: 0.8, tvl: 7_100_000_000 },
  { symbol: 'AVAX', name: 'Avalanche', category: 'Layer 1', base: 25.74, cap: 10_600_000_000, volume: 321_000_000, dominance: 0.3, tvl: 1_100_000_000 },
  { symbol: 'LINK', name: 'Chainlink', category: 'Oracle', base: 22.51, cap: 15_200_000_000, volume: 312_000_000, dominance: 0.4, tvl: 1_060_000_000 },
  { symbol: 'TON', name: 'Toncoin', category: 'Layer 1', base: 3.42, cap: 8_400_000_000, volume: 129_000_000, dominance: 0.2, tvl: 278_000_000 },
  { symbol: 'DOT', name: 'Polkadot', category: 'Layer 0', base: 4.11, cap: 6_500_000_000, volume: 183_000_000, dominance: 0.2, tvl: 154_000_000 },
  { symbol: 'MATIC', name: 'Polygon', category: 'Layer 2', base: 0.284, cap: 2_800_000_000, volume: 172_000_000, dominance: 0.1, tvl: 1_050_000_000 },
  { symbol: 'ARB', name: 'Arbitrum', category: 'Layer 2', base: 0.612, cap: 3_300_000_000, volume: 203_000_000, dominance: 0.1, tvl: 2_400_000_000 },
  { symbol: 'OP', name: 'Optimism', category: 'Layer 2', base: 0.861, cap: 1_600_000_000, volume: 162_000_000, dominance: 0.1, tvl: 760_000_000 },
  { symbol: 'UNI', name: 'Uniswap', category: 'DeFi', base: 10.24, cap: 6_100_000_000, volume: 107_000_000, dominance: 0.2, tvl: 3_900_000_000 },
  { symbol: 'AAVE', name: 'Aave', category: 'DeFi', base: 309.12, cap: 4_600_000_000, volume: 132_000_000, dominance: 0.1, tvl: 36_700_000_000 },
  { symbol: 'PEPE', name: 'Pepe', category: 'Meme', base: 0.0000117, cap: 4_900_000_000, volume: 782_000_000, dominance: 0.1, tvl: 0 },
];

export const PROPERTIES = [
  { id: 'prop-001', name: 'Hudson Glass House', type: 'Sale property', status: 'For sale', city: 'Hoboken', state: 'NJ', address: '812 Hudson Street', beds: 3, baths: 2.5, sqft: 2240, base: 1849000, mortgage: 10840, rental: 8200, nightly: 620, image: './assets/images/properties/unsplash-contemporary-luxury-modern-home-1.jpg', capRate: 4.7, down: [10, 20, 30], description: 'A sunlit, modern townhouse with wide-open river views and an effortless connection to the waterfront.' },
  { id: 'prop-002', name: 'West Village Loft', type: 'Rental space', status: 'For rent', city: 'New York', state: 'NY', address: '46 Bleecker Street', beds: 2, baths: 2, sqft: 1460, base: 9500, mortgage: 0, rental: 9500, nightly: 740, image: './assets/images/properties/unsplash-contemporary-luxury-modern-home-2.jpg', capRate: 5.2, down: [1, 2, 3], description: 'A character-rich loft with generous light, warm finishes and walkable access to the best of downtown.' },
  { id: 'prop-003', name: 'Palisade Retreat', type: 'Short-term stay', status: 'Stay ready', city: 'Englewood Cliffs', state: 'NJ', address: '19 River Road', beds: 4, baths: 3.5, sqft: 3120, base: 1435000, mortgage: 8470, rental: 6800, nightly: 550, image: './assets/images/properties/unsplash-contemporary-luxury-modern-home-3.jpg', capRate: 6.1, down: [10, 20, 25], description: 'A private, design-forward retreat framed by mature landscaping, moments from the city skyline.' },
  { id: 'prop-004', name: 'Harborline Residences', type: 'REIT', status: 'Investable', city: 'Jersey City', state: 'NJ', address: '88 Morgan Street', beds: 0, baths: 0, sqft: 0, base: 42.86, mortgage: 0, rental: 0, nightly: 0, image: './assets/images/properties/unsplash-contemporary-luxury-modern-home-4.jpg', capRate: 5.6, down: [1, 5, 10], description: 'A diversified residential REIT with high-occupancy urban communities and quarterly income potential.' },
  { id: 'prop-005', name: 'Modernist Courtyard Home', type: 'Mortgage', status: 'For sale', city: 'Austin', state: 'TX', address: '1707 Barton Springs Road', beds: 4, baths: 3, sqft: 2880, base: 1325000, mortgage: 7840, rental: 6100, nightly: 480, image: './assets/images/properties/unsplash-contemporary-luxury-modern-home-5.jpg', capRate: 5.1, down: [10, 20, 30], description: 'A modern home built around a quiet courtyard, with flexible living spaces and a polished natural palette.' },
];

const productImage = {
  ev1: './assets/images/vehicles/electric-crossover.jpg',
  ev2: './assets/images/vehicles/ev-interior.jpg',
  ev3: './assets/images/vehicles/ev-charging.jpg',
  truck: './assets/images/vehicles/cybertruck.jpg',
  charger: './assets/images/vehicles/tesla-supercharger.jpg',
  robot1: './assets/images/vehicles/humanoid-robot-human-robotics-lab-photo--1.jpg',
  robot2: './assets/images/vehicles/humanoid-robot-human-robotics-lab-photo--2.jpg',
  robot3: './assets/images/vehicles/humanoid-robot-human-robotics-lab-photo--3.jpg',
  watch1: './assets/images/wearables/watch-one.jpg',
  watch2: './assets/images/wearables/watch-ultra.jpg',
  apparel1: './assets/images/apparel/clothing-apparel-hoodie-black-fashion-t--1.jpg',
  apparel2: './assets/images/apparel/hoodie-product.jpg',
  apparel3: './assets/images/apparel/logo-tee.png',
  energy1: './assets/images/energy/house-battery.jpg',
  energy2: './assets/images/energy/solar-home.jpg',
  energy3: './assets/images/energy/solar-generator.jpg',
};

export const PRODUCTS = [
  { id: 's-plaid', name: 'Model S Plaid', category: 'Electric Vehicles', base: 89990, rating: 4.9, reviews: 1284, variants: ['Stealth Grey', 'Ultra Red', 'Pearl White'], specs: '1,020 hp · 0–60 mph in 1.99s · 390 mi range', description: 'A long-range electric performance sedan with an unmistakable silhouette and a next-generation cabin.', image: productImage.ev1, reservation: true, depositRate: .05, shipping: 0 },
  { id: '3-long-range', name: 'Model 3 Long Range', category: 'Electric Vehicles', base: 42490, rating: 4.8, reviews: 3480, variants: ['Stealth Grey', 'Pearl White', 'Deep Blue'], specs: '363 mi range · Dual motor · Premium interior', description: 'An all-electric daily driver designed for effortless range, refined comfort and responsive performance.', image: productImage.ev2, reservation: true, depositRate: .05, shipping: 0 },
  { id: 'x-plaid', name: 'Model X Plaid', category: 'Electric Vehicles', base: 99990, rating: 4.8, reviews: 933, variants: ['Stealth Grey', 'Ultra Red', 'Pearl White'], specs: '1,020 hp · 6 seats · 335 mi range', description: 'Versatile electric luxury, expansive space and exhilarating performance in one all-wheel-drive SUV.', image: productImage.ev3, reservation: true, depositRate: .05, shipping: 0 },
  { id: 'y-long-range', name: 'Model Y Long Range', category: 'Electric Vehicles', base: 48990, rating: 4.9, reviews: 4120, variants: ['Stealth Grey', 'Pearl White', 'Quicksilver'], specs: '337 mi range · 5 seats · All-wheel drive', description: 'A spacious electric crossover with thoughtful storage and a quiet, confident ride.', image: productImage.ev1, reservation: true, depositRate: .05, shipping: 0 },
  { id: 'cybertruck', name: 'Cybertruck', category: 'Electric Vehicles', base: 79990, rating: 4.7, reviews: 1692, variants: ['Stainless Steel', 'Foundation Series'], specs: '11,000 lb towing · Steer-by-wire · 325 mi range', description: 'An all-electric utility vehicle with a stainless-steel exoskeleton and adaptive performance.', image: productImage.truck, reservation: true, depositRate: .08, shipping: 0 },
  { id: 'roadster', name: 'Roadster', category: 'Electric Vehicles', base: 200000, rating: 4.9, reviews: 526, variants: ['Obsidian Black', 'Arctic Silver', 'Crimson'], specs: 'Tri-motor · Glass roof · Performance-focused chassis', description: 'A next-generation electric grand tourer with striking proportions and a driver-first cockpit.', image: productImage.ev3, reservation: true, depositRate: .1, shipping: 0 },
  { id: 'semi', name: 'Tesla Semi', category: 'Electric Vehicles', base: 250000, rating: 4.8, reviews: 248, variants: ['Standard Range', 'Long Range'], specs: 'Class 8 · 500 mi range · High-efficiency powertrain', description: 'An all-electric heavy-duty platform engineered for fleet uptime and lower operating costs.', image: productImage.truck, reservation: true, depositRate: .08, shipping: 0 },
  { id: 'delivery-van', name: 'Electric Delivery Van', category: 'Electric Vehicles', base: 74800, rating: 4.7, reviews: 183, variants: ['Cargo', 'Crew', 'High Roof'], specs: 'Up to 280 mi range · 3,900 lb payload', description: 'A configurable electric work van for modern delivery teams and growing businesses.', image: productImage.ev3, reservation: true, depositRate: .06, shipping: 0 },
  { id: 'performance-coupe', name: 'Performance Electric Coupe', category: 'Electric Vehicles', base: 68500, rating: 4.6, reviews: 314, variants: ['Midnight', 'Glacier', 'Copper'], specs: 'Dual motor · 320 mi range · Fast-charge capable', description: 'A sleek electric coupe that puts everyday range and weekend performance in the same lane.', image: productImage.ev2, reservation: true, depositRate: .05, shipping: 0 },
  { id: 'optimus', name: 'Optimus', category: 'Humanoid Robots', base: 30000, rating: 4.8, reviews: 693, variants: ['Gen 3 · Graphite', 'Gen 3 · Pearl'], specs: 'Bipedal platform · 22 degrees of freedom · Vision AI', description: 'A general-purpose humanoid platform for repetitive tasks, research and next-generation automation.', image: productImage.robot1, reservation: true, depositRate: .1, shipping: 0 },
  { id: 'optimus-pro', name: 'Optimus Pro', category: 'Humanoid Robots', base: 45000, rating: 4.9, reviews: 284, variants: ['Industrial', 'Research'], specs: 'Extended runtime · Precision gripper · Fleet-ready', description: 'An advanced robotics system for structured environments, built for repeatable precision work.', image: productImage.robot2, reservation: true, depositRate: .12, shipping: 0 },
  { id: 'optimus-mini', name: 'Optimus Mini', category: 'Humanoid Robots', base: 14500, rating: 4.6, reviews: 432, variants: ['Graphite', 'Pearl'], specs: 'Compact frame · Modular hands · Developer SDK', description: 'A compact, configurable robot platform created for makers, labs and creative spaces.', image: productImage.robot3, reservation: true, depositRate: .1, shipping: 0 },
  { id: 'kinetic-one', name: 'Kinetic Watch One', category: 'Wearables', base: 349, rating: 4.7, reviews: 814, variants: ['42 mm Graphite', '46 mm Silver', '42 mm Rose'], specs: 'AMOLED · GPS · 7-day battery · 5 ATM', description: 'A clear, considered smartwatch for daily movement, health insights and focused routines.', image: productImage.watch1, reservation: false, shipping: 8 },
  { id: 'kinetic-ultra', name: 'Kinetic Watch Ultra', category: 'Wearables', base: 649, rating: 4.9, reviews: 462, variants: ['Titanium', 'Black Titanium'], specs: '49 mm titanium · Dual-frequency GPS · 14-day battery', description: 'A durable precision wearable with advanced GPS and battery life for long days outdoors.', image: productImage.watch2, reservation: false, shipping: 8 },
  { id: 'kinetic-se', name: 'Kinetic Watch SE', category: 'Wearables', base: 229, rating: 4.6, reviews: 1104, variants: ['40 mm Midnight', '44 mm Starlight', '40 mm Blush'], specs: 'Always-on OLED · 5-day battery · Water resistant', description: 'All the daily essentials in a lightweight, intuitive watch designed to move with you.', image: productImage.watch1, reservation: false, shipping: 8 },
  { id: 'logo-tee', name: 'Logo Tee', category: 'Apparel', base: 38, rating: 4.8, reviews: 2034, variants: ['XS', 'S', 'M', 'L', 'XL', 'XXL'], specs: 'Organic cotton · Relaxed fit · 220 gsm', description: 'A heavyweight everyday tee with a soft hand feel and a clean embroidered mark.', image: productImage.apparel3, reservation: false, shipping: 6 },
  { id: 'hoodie', name: 'Hoodie', category: 'Apparel', base: 88, rating: 4.9, reviews: 1681, variants: ['XS', 'S', 'M', 'L', 'XL', 'XXL'], specs: 'Cotton fleece · Brushed interior · Unisex fit', description: 'A premium mid-weight fleece layer with a structured hood and considered details.', image: productImage.apparel2, reservation: false, shipping: 8 },
  { id: 'cap', name: 'Cap', category: 'Apparel', base: 32, rating: 4.6, reviews: 617, variants: ['One size · Black', 'One size · Stone'], specs: 'Washed cotton twill · Adjustable closure', description: 'A low-profile, six-panel cap finished in soft washed cotton twill.', image: productImage.apparel1, reservation: false, shipping: 5 },
  { id: 'jacket', name: 'Jacket', category: 'Apparel', base: 168, rating: 4.8, reviews: 412, variants: ['S', 'M', 'L', 'XL'], specs: 'Recycled shell · Wind resistant · Packable', description: 'An adaptable technical jacket with a quiet profile and lightweight weather protection.', image: productImage.apparel2, reservation: false, shipping: 8 },
  { id: 'home-battery', name: 'Home Battery', category: 'Home Energy', base: 8900, rating: 4.9, reviews: 949, variants: ['13.5 kWh · White', '13.5 kWh · Graphite'], specs: '13.5 kWh · Backup ready · App-connected', description: 'Intelligent home energy storage that pairs with solar and adapts to household demand.', image: productImage.energy1, reservation: false, shipping: 150 },
  { id: 'solar-roof', name: 'Solar Roof Panel', category: 'Home Energy', base: 480, rating: 4.7, reviews: 328, variants: ['Black glass · 1 panel', 'Textured glass · 1 panel'], specs: '420 W · Tempered glass · 25-year warranty', description: 'A high-efficiency architectural panel designed to turn available roof area into dependable power.', image: productImage.energy2, reservation: false, shipping: 45 },
  { id: 'survey-drone', name: 'Survey Drone', category: 'Emerging Tech', base: 2890, rating: 4.8, reviews: 502, variants: ['Survey Kit', 'Survey Kit + RTK'], specs: '4K mapping · 45-minute flight · Obstacle sensing', description: 'A compact mapping drone built for precise aerial surveys and repeatable field capture.', image: './assets/images/thumbs/robot-thumb.jpg', reservation: false, shipping: 35 },
  { id: 'dc-fast-charger', name: 'DC Fast EV Charger', category: 'Emerging Tech', base: 18400, rating: 4.8, reviews: 276, variants: ['150 kW · CCS', '240 kW · CCS/NACS'], specs: 'Up to 240 kW · Network enabled · Indoor/outdoor', description: 'A high-throughput charging system engineered for fleets, workplaces and shared destinations.', image: productImage.charger, reservation: false, shipping: 250 },
  { id: 'wall-connector', name: 'Home Wall Connector', category: 'Home Energy', base: 475, rating: 4.9, reviews: 1834, variants: ['NACS · 24 ft', 'Universal · 24 ft'], specs: 'Up to 44 mi range per hour · Wi-Fi connected', description: 'A compact home charging solution with smart scheduling and connected power management.', image: productImage.charger, reservation: false, shipping: 18 },
  { id: 'portable-solar', name: 'Portable Solar Generator', category: 'Home Energy', base: 1899, rating: 4.8, reviews: 902, variants: ['1.2 kWh', '2.4 kWh + panel'], specs: '1,800 W output · Solar ready · 30 ms UPS', description: 'Portable stored energy for a flexible home backup, a remote workspace or the open road.', image: productImage.energy3, reservation: false, shipping: 35 },
  { id: 'solar-inverter', name: 'Solar Inverter Converter', category: 'Home Energy', base: 2250, rating: 4.7, reviews: 385, variants: ['8 kW · Single phase', '11.4 kW · Split phase'], specs: 'Hybrid inverter · 97.6% peak efficiency · App monitoring', description: 'A grid-aware power converter that brings solar generation, battery storage and home loads together.', image: productImage.energy2, reservation: false, shipping: 28 },
  { id: 'inference-cluster', name: 'Inference Cluster', category: 'Emerging Tech', base: 24900, rating: 4.9, reviews: 197, variants: ['4-node · 256 GB', '8-node · 512 GB'], specs: 'Liquid cooled · 4× accelerator nodes · 10 GbE', description: 'A compact accelerated-compute system for private inference, prototyping and applied research.', image: './assets/images/cards/market-desk.jpg', reservation: false, shipping: 490 },
];

export const TIERS = [
  { name: 'Starter', min: 100, max: 999, note: 'Build a steady rhythm' },
  { name: 'Builder', min: 1_000, max: 9_999, note: 'Grow with consistency' },
  { name: 'Growth', min: 10_000, max: 49_999, note: 'Put compounding to work' },
  { name: 'Premium', min: 50_000, max: 249_999, note: 'A wider allocation canvas' },
  { name: 'Wealth', min: 250_000, max: 999_999, note: 'Coordinate a larger portfolio' },
  { name: 'Private', min: 1_000_000, max: 10_000_000, note: 'Plan across generations' },
];

export const ADVICE = [
  { type: 'Investing', icon: '↗', title: 'Make time your advantage', body: 'A small contribution made consistently can matter more than a perfectly timed one. Set a cadence you can keep.' },
  { type: 'Budgeting', icon: '⌁', title: 'Give every dollar a role', body: 'Separate essentials, future goals and flexible spending before the month begins. Clarity makes trade-offs easier.' },
  { type: 'Discipline', icon: '◷', title: 'Automate the habit', body: 'A recurring transfer can keep your plan moving when motivation is busy elsewhere.' },
  { type: 'Long-term wealth', icon: '⌘', title: 'Think in seasons, not days', body: 'A long horizon gives compounding room to work. Review your plan on a schedule, not after every headline.' },
  { type: 'Emergency funds', icon: '◈', title: 'Keep a calm reserve', body: 'Build accessible savings for essential expenses before reaching for higher-volatility opportunities.' },
  { type: 'Asset allocation', icon: '◉', title: 'Diversify with intention', body: 'Choose a mix you understand, suited to your goals, time horizon and comfort with risk.' },
  { type: 'Smart purchases', icon: '✳', title: 'Pause before the upgrade', body: 'Compare total ownership cost, utility and your savings priorities before making a big purchase.' },
];

const hash32 = (text) => {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
};
export function dailySeed() { return new Date().toISOString().slice(0, 10); }
export function quote(base, key, spread = 0.012, digits = 2) {
  const r = hash32(`${key}:${dailySeed()}`) / 0xffffffff;
  const adjusted = base * (1 + (r - 0.5) * spread * 2);
  return Number(adjusted.toFixed(digits));
}
export function dailyChange(key, spread = 5.2) {
  return Number(((hash32(`${key}:move:${dailySeed()}`) / 0xffffffff - 0.49) * spread).toFixed(2));
}
export function tierFor(amount = 0) { return TIERS.find((tier) => amount >= tier.min && amount <= tier.max)?.name || (amount > 10_000_000 ? 'Private' : '—'); }
export function formatMoney(value, compact = false) {
  const n = Number(value || 0);
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: compact ? 1 : 2, notation: compact && Math.abs(n) >= 100000 ? 'compact' : 'standard' }).format(n);
}
export function formatNumber(value, digits = 0) { return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(Number(value || 0)); }
export function formatPercent(value, digits = 2) { const n = Number(value || 0); return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`; }
export function formatMarketCap(value) {
  const n = Number(value || 0);
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return formatMoney(n, true);
}
