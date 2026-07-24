(function (root) {
  "use strict";

  const templates = [
    "In {place}, {lead} must {goal} after {complication}, before {stakes}.",
    "When {complication}, {lead} discovers {discovery} and must {goal} before {stakes}.",
    "A routine day in {place} changes when {lead} uncovers {discovery}; now they must {goal} before {stakes}.",
    "As {stakes}, {lead} must {goal}, even after {complication} and the discovery of {discovery}.",
    "At {place}, {lead} discovers {discovery}. After {complication}, they must {goal} before {stakes}.",
  ];

  function assets(id) {
    const rootPath = `themes/${id}`;
    return {
      backgrounds: [
        `${rootPath}/backgrounds/day-centered-01.webp`,
        `${rootPath}/backgrounds/day-left-01.webp`,
        `${rootPath}/backgrounds/warm-right-01.webp`,
        `${rootPath}/backgrounds/night-centered-01.webp`,
      ],
      actors: [
        `${rootPath}/actors/actor-01-left.webp`,
        `${rootPath}/actors/actor-01-right.webp`,
        `${rootPath}/actors/actor-02-left.webp`,
        `${rootPath}/actors/actor-02-right.webp`,
      ],
      foregrounds: [
        `${rootPath}/foregrounds/center-01.webp`,
        `${rootPath}/foregrounds/left-01.webp`,
        `${rootPath}/foregrounds/right-01.webp`,
      ],
    };
  }

  const themes = [
    {
      id: "viking", label: "Viking Saga", assets: assets("viking"),
      titles: { prefixes: ["The Last", "Beyond the", "Song of the", "Oath of the", "Under the", "Return to"], nouns: ["Longship", "Fjord", "Winter Crown", "Runestone", "Raven Banner", "Northern Fire", "Whale Road", "Oak Shield"] },
      descriptions: {
        templates,
        leads: ["a reluctant shield-bearer", "an exiled navigator", "the heir to a divided clan", "a young runekeeper", "a veteran shipwright", "a trader returning from distant shores"],
        goals: ["guide the fleet home", "restore an ancient alliance", "uncover the truth behind a broken oath", "bring two rival settlements to council", "recover a map lost at sea", "protect the winter stores"],
        complications: ["a rival clan blocks the winter passage", "an unexpected storm scatters the fleet", "a messenger arrives with a disputed claim", "the harbor freezes weeks too early", "a trusted guide vanishes", "an old boundary stone is found moved"],
        stakes: ["the first snow closes the fjord", "the clans gather for their final council", "the last trading ship departs", "the winter feast begins", "the northern road disappears beneath the ice", "the new chieftain takes the oath"],
        places: ["a remote settlement beneath the northern cliffs", "a trading port at the edge of the frozen sea", "a longhouse overlooking a stormy fjord", "an island monastery beyond the shipping lanes", "a forest village beside an ancient runestone", "a shipyard preparing for the final autumn voyage"],
        discoveries: ["a forgotten map carved into a runestone", "evidence of an alliance erased from history", "a sealed message beneath the longhouse floor", "a ship log describing an unknown coast", "the true owner of a ceremonial shield", "a family connection to the rival clan"],
      },
      tags: ["Adventure", "Period Piece", "Ensemble", "Winter"], studioWords: ["Runestone", "North Sea", "Raven", "Longship"],
    },
    {
      id: "gladiator", label: "Arena Epic", assets: assets("gladiator"),
      titles: { prefixes: ["The Final", "Beneath the", "Champion of the", "Echoes of", "Road to", "Beyond the"], nouns: ["Arena", "Laurel", "Bronze Gate", "Marble City", "Victory Road", "Golden Sand", "Lion Standard", "Emperor's Games"] },
      descriptions: {
        templates,
        leads: ["a retired arena champion", "an ambitious charioteer", "a principled palace scribe", "the daughter of a provincial governor", "a veteran games steward", "an apprentice physician"],
        goals: ["expose a fixed championship", "earn freedom for the arena troupe", "deliver a petition to the senate", "protect an injured rival", "restore honor to a disgraced school", "prevent the games from dividing the city"],
        complications: ["the favorite withdraws without explanation", "a patron changes the rules overnight", "the ceremonial laurel disappears", "rival schools refuse to share the arena", "a sandstorm delays the opening procession", "an anonymous wager implicates the palace"],
        stakes: ["the championship gates open", "the emperor arrives for the final contest", "the city chooses its new games master", "the victory procession crosses the forum", "the arena schools sign their yearly pact", "the final chariot race begins"],
        places: ["a crowded arena district in the imperial capital", "a coastal training school surrounded by olive groves", "the marble corridors beneath the grand arena", "a provincial city preparing its centennial games", "a chariot stable beside the old forum", "a hillside villa overlooking the race course"],
        discoveries: ["a ledger linking every suspicious wager", "a forgotten clause in the arena charter", "the champion's secret plan to retire", "a hidden tunnel leading beneath the imperial box", "a physician's report that changes the contest", "a bronze token belonging to the games master"],
      },
      tags: ["Historical Drama", "Competition", "Ensemble", "Epic"], studioWords: ["Laurel", "Marble", "Arena", "Bronze Gate"],
    },
    {
      id: "space", label: "Space Adventure", assets: assets("space"),
      titles: { prefixes: ["Beyond", "Return to", "Signals from", "The Last", "Orbit of", "Journey to"], nouns: ["Andromeda", "Red Horizon", "Silent Moon", "Kepler Station", "Solar Wind", "Outer Beacon", "Star Harbor", "Midnight Orbit"] },
      descriptions: {
        templates,
        leads: ["a cautious survey pilot", "the station's newest engineer", "a linguist assigned to first contact", "a retired deep-space navigator", "an optimistic cargo captain", "a botanist maintaining the colony greenhouse"],
        goals: ["repair the outer communications array", "guide a lost research vessel home", "decode a signal from beyond the mapped systems", "keep the colony's orbit stable", "complete the first survey of a silent moon", "reunite two crews separated by a solar storm"],
        complications: ["an unexplained signal overrides the navigation system", "a meteor shower damages the docking ring", "the station wakes from standby months early", "a survey drone returns with impossible coordinates", "the colony's clocks begin drifting apart", "an unregistered vessel requests emergency docking"],
        stakes: ["the orbital window closes", "the colony enters the planet's shadow", "the rescue fleet leaves the system", "the station exhausts its reserve power", "the next solar storm reaches the moon", "the jump corridor shifts beyond range"],
        places: ["a research station orbiting a blue gas giant", "a greenhouse colony on the edge of mapped space", "a cargo vessel crossing a quiet nebula", "an abandoned observatory on a small moon", "a busy star harbor above a desert planet", "a survey camp beneath two pale suns"],
        discoveries: ["a repeating message hidden inside ordinary telemetry", "a living seed preserved in lunar ice", "a star chart drawn from an unknown viewpoint", "the missing vessel's intact flight recorder", "a dormant maintenance intelligence", "an artificial structure beneath the colony"],
      },
      tags: ["Science Fiction", "Adventure", "Mystery", "Ensemble"], studioWords: ["Orbital", "Kepler", "Star Harbor", "Red Horizon"],
    },
    {
      id: "victorian", label: "Victorian Mystery", assets: assets("victorian"),
      titles: { prefixes: ["The Curious", "Secrets of", "A Winter at", "The Last", "Letters from", "Mystery of the"], nouns: ["Ashcroft House", "Glass Conservatory", "Midnight Train", "Silver Locket", "Baker Street", "Winter Garden", "Clockmaker's Lane", "Rosewood Library"] },
      descriptions: {
        templates,
        leads: ["an observant society columnist", "a reserved country doctor", "the new curator of a private museum", "an independent railway clerk", "a young architectural historian", "a widowed owner of a bookshop"],
        goals: ["trace the author of a coded letter", "save a historic house from demolition", "return a misplaced inheritance", "solve the disappearance of a rare manuscript", "clear a friend's name", "discover why the midnight train changed its route"],
        complications: ["a formal invitation arrives twenty years late", "the household portraits have been rearranged", "a witness remembers two different evenings", "the railway timetable contains a hidden message", "a dense fog isolates the neighborhood", "the family solicitor refuses to open the final envelope"],
        stakes: ["the estate auction begins", "the last train leaves London", "the museum opens its new exhibition", "the family gathers for the winter ball", "the disputed will is read", "the conservatory is scheduled to be dismantled"],
        places: ["a gaslit neighborhood of narrow London streets", "a country estate surrounded by winter gardens", "a private museum near the Thames", "a railway hotel at a fogbound junction", "a bookshop beneath a clockmaker's workshop", "a grand conservatory attached to an aging townhouse"],
        discoveries: ["a second message beneath the letter's seal", "a portrait concealing the original floor plan", "a library index written in two different hands", "an unused railway ticket dated decades earlier", "a collection catalog with one impossible entry", "a locket containing a tiny architectural sketch"],
      },
      tags: ["Mystery", "Period Piece", "Drama", "Winter"], studioWords: ["Rosewood", "Gaslight", "Ashcroft", "Silver Locket"],
    },
    {
      id: "steampunk", label: "Steampunk Adventure", assets: assets("steampunk"),
      titles: { prefixes: ["The Brass", "Voyage of the", "Clockwork", "Above the", "Secrets of the", "Return of the"], nouns: ["Airship", "Aether Engine", "Copper City", "Sky Railway", "Mechanical Garden", "Cloud Atlas", "Brass Compass", "Midnight Inventor"] },
      descriptions: {
        templates,
        leads: ["an inventive airship mechanic", "a meticulous clockmaker", "the captain of a postal dirigible", "a skeptical academy lecturer", "a daring map engraver", "an apprentice keeper of mechanical birds"],
        goals: ["repair the city's failing aether engine", "deliver a parcel across the forbidden air lanes", "prove a celebrated invention was misattributed", "chart a safe route through the storm belt", "restart the mechanical gardens", "recover the prototype brass compass"],
        complications: ["every clock in the city stops at noon", "an airship docks without a crew", "the academy seals the inventor's workshop", "a mechanical bird repeats a coded warning", "the sky railway changes tracks by itself", "a rival expedition claims the same discovery"],
        stakes: ["the floating city loses altitude", "the grand exhibition opens", "the storm belt reaches the capital", "the last airship departs", "the academy awards its inventor's medal", "the mechanical gardens enter permanent shutdown"],
        places: ["a copper-roofed city suspended above the clouds", "an airship workshop crowded with brass instruments", "a mountain station on the transcontinental sky railway", "a mechanical garden beneath a glass dome", "an academy laboratory overlooking the harbor", "a postal dirigible crossing a permanent storm belt"],
        discoveries: ["a second set of plans inside the brass compass", "an engine powered by music rather than steam", "a hidden station beyond the published railway map", "the inventor's notes encoded as bird songs", "a miniature city inside the prototype", "proof that the rival expeditions once worked together"],
      },
      tags: ["Adventure", "Fantasy", "Invention", "Period Piece"], studioWords: ["Clockwork", "Aether", "Copper City", "Brass Compass"],
    },
    {
      id: "caveman", label: "Stone Age Comedy", assets: assets("caveman"),
      titles: { prefixes: ["The First", "Beyond the", "Return to", "Under the", "Quest for the", "Songs of the"], nouns: ["Fire", "Painted Cave", "Mammoth Trail", "Stone Wheel", "River Clan", "Great Hunt", "Red Valley", "Moon Festival"] },
      descriptions: {
        templates,
        leads: ["an unusually curious toolmaker", "the clan's patient storyteller", "a young painter of cave murals", "a cautious mammoth tracker", "the valley's most inventive cook", "a traveler from the distant river clan"],
        goals: ["bring fire safely across the valley", "find a new route to the summer camp", "finish the clan's largest cave painting", "return a lost stone tool", "organize the first valley festival", "teach two clans to share the fishing grounds"],
        complications: ["a herd blocks the familiar mountain pass", "heavy rain washes away every trail", "the ceremonial drum goes missing", "a curious wolf follows the expedition", "the neighboring clan misunderstands a gift", "the new stone wheel refuses to roll straight"],
        stakes: ["the river rises above its banks", "the moon festival begins", "the herds leave the valley", "the first winter storm reaches camp", "the clan starts its seasonal journey", "the cave painters seal the ceremonial chamber"],
        places: ["a lively settlement beside a wide prehistoric river", "a painted cave overlooking the red valley", "a summer camp beneath a natural stone arch", "a grassy plain crossed by mammoth trails", "a sheltered beach filled with unusual shells", "a forest clearing shared by two neighboring clans"],
        discoveries: ["a cave filled with handprints from an earlier clan", "a stone that makes bright colors when ground", "the remains of an ancient campsite", "a simpler way to carry water", "a trail marked by carefully stacked stones", "that the rival storyteller knows the same song"],
      },
      tags: ["Comedy", "Adventure", "Family", "Prehistory"], studioWords: ["Stone Wheel", "Painted Cave", "River Clan", "Red Valley"],
    },
    {
      id: "lawyer", label: "Legal Drama", assets: assets("lawyer"),
      titles: { prefixes: ["The Final", "Beyond a", "Terms of", "A Question of", "Inside the", "The Last"], nouns: ["Verdict", "Appeal", "Testimony", "Corner Office", "Case File", "Closing Argument", "Fine Print", "City Court"] },
      descriptions: {
        templates,
        leads: ["an idealistic junior attorney", "a veteran public defender", "the firm's meticulous researcher", "a mediator known for impossible settlements", "a newly appointed city judge", "an investigator returning to legal work"],
        goals: ["reopen a case everyone considers settled", "negotiate peace between two family businesses", "find the missing page of a crucial contract", "defend a neighborhood institution", "prove a witness was sincerely mistaken", "resolve the dispute without a public trial"],
        complications: ["a key witness changes their account", "the original case file arrives incomplete", "opposing counsel reveals an unexpected connection", "a deadline is moved forward by a week", "the firm's oldest client withdraws support", "two contracts contain the same unusual error"],
        stakes: ["closing arguments begin", "the courthouse closes for renovation", "the partnership vote is held", "the disputed property goes to auction", "the judge issues a final ruling", "the settlement deadline expires"],
        places: ["a busy courthouse in the center of the city", "a small legal clinic above a family bakery", "a glass-walled firm overlooking the river", "a municipal archive filled with decades of case files", "a quiet mediation room after business hours", "a neighborhood council chamber awaiting a difficult vote"],
        discoveries: ["a handwritten amendment in the original contract", "a photograph that changes the timeline", "that both clients received the same anonymous letter", "an overlooked precedent from the city's first court", "a witness with no reason to favor either side", "a filing receipt hidden inside the case folder"],
      },
      tags: ["Legal Drama", "Mystery", "Contemporary", "City Story"], studioWords: ["Verdict", "Case File", "Civic", "Corner Office"],
    },
    {
      id: "teacher", label: "School Story", assets: assets("teacher"),
      titles: { prefixes: ["The New", "Lessons from", "After the", "A Year at", "Beyond the", "The Last"], nouns: ["Classroom", "School Bell", "Science Fair", "Autumn Term", "Debate Club", "Library Steps", "Field Trip", "Final Project"] },
      descriptions: {
        templates,
        leads: ["an enthusiastic new teacher", "the school's longtime librarian", "a student directing the spring play", "a practical science teacher", "the adviser of an underdog debate team", "a former pupil returning as a counselor"],
        goals: ["save the annual science fair", "rebuild the neglected school garden", "prepare the debate team for regionals", "stage a play written by the students", "find a home for the library's special collection", "bring two rival classes together for one project"],
        complications: ["a burst pipe closes the main classroom", "the project budget disappears", "the school mascot goes missing", "a surprise inspection moves the deadline forward", "the auditorium is double-booked", "the students choose a far more ambitious plan"],
        stakes: ["the final school bell rings", "the regional judges arrive", "the autumn open house begins", "the curtain rises on opening night", "the library renovation starts", "the graduating class presents its final project"],
        places: ["a small-town school preparing for its centennial", "a city academy built around an old courtyard", "a rural classroom overlooking fields and orchards", "a crowded library during the autumn term", "a science lab shared by two rival classes", "an aging auditorium filled with handmade scenery"],
        discoveries: ["a box of student projects from fifty years earlier", "the original plans for a forgotten school garden", "a talent no one expected from the quietest student", "a set of letters from the school's first teacher", "that the rival classes have complementary ideas", "an unused room behind the library shelves"],
      },
      tags: ["School Story", "Ensemble", "Comedy Drama", "Coming of Age"], studioWords: ["School Bell", "Autumn Term", "Library Steps", "Field Trip"],
    },
  ];

  if (typeof module !== "undefined" && module.exports) module.exports = themes;
  if (root) root.CoverStoryThemes = themes;
})(typeof window !== "undefined" ? window : null);
