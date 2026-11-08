# Recipe coverage audit — GTNH 2.9.0

Measured against the local GTNH 2.9.0 client pack on 2026-11-07. This inventories
all **172 registered GT maps and 362 unique NEI categories** after NEI plugins
finish loading. It is not a claim to enumerate arbitrary Java matching logic,
recipes without a registered handler, or world/player-dependent content.

The final inventory unions crafting and usage registries and deduplicates by class
and category. This recovered five Witching Gadgets spinning-wheel records missing
from one crafting-only run and found four additional usage categories. Every
category in the original 358-category probe is present in this final inventory.

## Exported and indexed

| Source records | Count |
| --- | ---: |
| Native GT processing | 174,926 |
| Crafting table | 58,108 |
| Furnace | 9,683 |
| Native Assembly Line | 494 |
| Approximate NEI displays | 40,134 |
| Total | 283,345 |

**494 Assembly Line recipes** include **190 Scanner** and **304 Research Station**
requirements. The 304 TecTech entries overlay native production entries; they are
not another 304 production recipes. Research item, voltage, time/computation,
amperage and ingredient alternatives are retained separately from production EU/t.
Overlay matching includes ordered ingredients, alternatives, fluids, output and NBT.

The bundle retains 1,108 GT scanner/research/duplicate Assembly Line diagrams for
reference. The query index excludes them: NEI puts the research target in their
fake output slot, which must not imply that researching an item manufactures it.
The final index contains **282,237 browsable records**.

Examples of newly captured categories (NEI displays are approximate):

| Category | Source records |
| --- | ---: |
| Inscriber | 12 |
| Xtreme Crafting | 135 |
| Xtreme Shapeless | 3 |
| Mana Infusion | 165 |
| Runic Altar | 48 |
| Petal Apothecary | 44 |
| Alchemic Chemistry Set | 60 |
| Altar of GregoriusT's Blood | 56 |
| Planetary Gas Siphon | 16 |
| Space Pumping Module | 40 |
| Circuit Assembly Line Imprinting | 37 |
| Static Thaumcraft infusion (native fallback) | 694 |

The export also captures supported arcane crafting/alchemy, Witchery, Ender IO,
Railcraft, BuildCraft assembly/integration, Tinkers casting, Forestry genetics,
brewing, NASA workbenches and additional categories listed below. GT display maps
now retain industrial chiselling, tree farming, cauldrons, scanners and other
categories previously removed by the blanket fake-recipe filter.

## Captured data that is not yet a complete browsable recipe

**15,747 raw archive records** are retained in map-level `archive` arrays, in
addition to snapshots attached to display/native recipes. Archive records overlap
some supported recipes and are not an additional unique recipe count.

- 6,886 crafting registrations skipped by the semantic adapter retain their raw
  recipe values and skip reason. Empty mandatory alternatives never become free ingredients.
- 516 GT records without usable outputs (including generator fuels and
  reference diagrams) or disabled entries are archived with their flags, input
  stacks, power/time/special values and supported metadata. Every registered GT
  entry now balances against native, display or archived records. These are not
  presented as recipes that manufacture an item. Fuel-value units remain map-specific.
- 2,180 native Thaumcraft crafting registrations are archived. Of 720 infusion
  registrations, 694 have static item outputs and 26 are dynamic transformations.
  One displayed infusion contains an unresolved aspect, explicitly marked in its costs.
- 6,165 NEI records without a safe primary output retain their ingredient/result/
  other stack roles, positions, NBT and supported cached values. Examples include
  Tinkers melting/alloying, Forestry fluid/byproduct processes, BuildCraft refinery,
  Ender IO vat, Chisel transformations and some genetics/drops displays.
- 349 unreadable NEI records belong to the two multiblock-preview handlers.
  Multiblock structures/layouts already have their own export; these are not
  349 missing production recipes.

Reader adapters can interpret captured values later without launching Minecraft
again. Snapshots are bounded to five nested levels and 512 entries per collection;
opaque or cyclic values carry markers. They do not serialize arbitrary predicates,
entire registries, client/world objects or every possible NBT state. Exact behavior
outside captured data can still require an exporter update.

## Unavailable and empty enumeration

6 categories could not be enumerated. Cell View and Encoded Pattern are
item-specific information views, not missing production recipes; their all-recipes
queries return no handler. The other failures are listed with the captured error:

| Handler | Observed failure / fallback |
| --- | --- |
| Cell View (`appeng.integration.modules.NEIHelpers.NEICellViewHandler`) | java.lang.NullPointerException: Cannot invoke "codechicken.nei.recipe.IRecipeHandler.numRecipes()" because "h" is null |
| Encoded Pattern (`appeng.integration.modules.NEIHelpers.NEIPatternViewHandler`) | java.lang.NullPointerException: Cannot invoke "codechicken.nei.recipe.IRecipeHandler.numRecipes()" because "h" is null |
| Polymeriser (`binnie.genetics.nei.PolymeriserRecipeHandler`) | java.lang.ArrayIndexOutOfBoundsException: Index 0 out of bounds for length 0 |
| Arcane Infusion (`com.gtnewhorizons.aspectrecipeindex.nei.InfusionRecipeHandler`) | com.google.common.util.concurrent.UncheckedExecutionException: java.lang.RuntimeException: java.lang.NullPointerException; native static infusion fallback exports 694 recipes |
| Microblock Crafting (`com.rwtema.extrautils.nei.FMPMicroBlocksHandler`) | java.lang.ArrayIndexOutOfBoundsException: Index 0 out of bounds for length 0 |
| LootBag (`eu.usrv.enhancedlootbags.integration.nei.LootBagRecipeHandler`) | java.lang.NullPointerException: Cannot invoke "net.minecraft.entity.player.EntityPlayer.func_110124_au()" because "pPlayer" is null |

**45 categories returned no all-recipes enumeration.** Some are empty/disabled
in this pack, some are information screens, and some only answer item queries or
need world data. This is recorded as “not enumerable”, not proof that no recipes
exist. These include AE2 in-world crafting, some IC2 machines, Avaritia compression,
mob/fishing/villager loot, repair/fuel/information handlers and others in the inventory.
No fake input/output recipe is invented from an empty or informational response.

## Validation and rollout

The real quick export completed in **172 seconds** with animation disabled.
`recipes.json.gz` is **7,040,889 bytes (~6.7 MiB)**. Full export validation passed,
including balanced counts, nonempty Assembly Line/audit data and typed recipe bodies.
The format-4 index built in **6.41 s**, peaking at **51,572 KiB**
(~50.4 MiB), and occupies **186,310,656 bytes** (~177.7 MiB).
Every indexed body matched independent full-file normalization.

Deploy readers accepting recipe source format 3 before publishing these bundles.
Rebuild shared indexes for index format 4. Existing source formats 1 and 2 remain
readable. No bundle was installed or published by this audit. Artifacts remain
under the ignored `work/recipe-audit/` directory. Animation can be enabled for the
eventual production bundle; the recipe capture does not depend on animation.

## Complete NEI inventory

“Native” uses an existing native adapter; its counts are in the GT/crafting tables.
“Display” exposes a labelled approximate recipe. “Archived/info” means no safe
primary output or an informational/preview handler; captured raw data is retained
where readable. Listed/captured counts are handler records, not unique recipes.

| Category and handler ID | Status | Listed | Browsable | Archived/unreadable |
| --- | --- | ---: | ---: | ---: |
| Alchemic Chemistry Set<br>`WayofTime.alchemicalWizardry.client.nei.NEIAlchemyRecipeHandler::alchemicalwizardry.alchemy` | Display | 60 | 60 | 0 |
| Altar of GregoriusT's Blood<br>`WayofTime.alchemicalWizardry.client.nei.NEIAltarRecipeHandler::altarrecipes` | Display | 56 | 56 | 0 |
| Binding Ritual<br>`WayofTime.alchemicalWizardry.client.nei.NEIBindingRitualHandler::alchemicalwizardry.bindingritual` | Display | 19 | 19 | 0 |
| Shaped Orb Crafting<br>`WayofTime.alchemicalWizardry.client.nei.NEIBloodOrbShapedHandler::crafting` | Display | 61 | 61 | 0 |
| Shapeless Orb Crafting<br>`WayofTime.alchemicalWizardry.client.nei.NEIBloodOrbShapelessHandler::crafting` | Display | 5 | 5 | 0 |
| Alchemic Calcinator<br>`WayofTime.alchemicalWizardry.client.nei.NEICalcinatorHandler::alchemicalwizardry.calcinator` | Display | 15 | 15 | 0 |
| Meteor Ritual<br>`WayofTime.alchemicalWizardry.client.nei.NEIMeteorRecipeHandler::alchemicalwizardry.meteor` | Archived/info | 57 | 0 | 57 |
| Molecular Transformer<br>`advsolar.client.nei.MTRecipeHandler::` | No enumeration | 0 | 0 | 0 |
| Witch's Cauldron<br>`alkalus.main.nei.NEI_Handler_Cauldron::witchery_brewing_plus` | Display | 26 | 26 | 0 |
| Distillery<br>`alkalus.main.nei.NEI_Handler_Distillery::witchery_distilling` | Archived/info | 9 | 0 | 9 |
| Kettle<br>`alkalus.main.nei.NEI_Handler_Kettle::witchery_brewing` | Display | 38 | 38 | 0 |
| Witches Oven<br>`alkalus.main.nei.NEI_Handler_Oven::witchery_cooking` | Display | 9 | 9 | 0 |
| Spinning Wheel<br>`alkalus.main.nei.NEI_Handler_SpinningWheel::witchery_spinning` | Display | 4 | 4 | 0 |
| Shaped Crafting<br>`appeng.integration.modules.NEIHelpers.NEIAEShapedRecipeHandler::crafting` | Native | — | — | — |
| Shapeless Crafting<br>`appeng.integration.modules.NEIHelpers.NEIAEShapelessRecipeHandler::crafting` | Native | — | — | — |
| Cell View<br>`appeng.integration.modules.NEIHelpers.NEICellViewHandler::` | Unavailable | — | — | — |
| Facade Crafting<br>`appeng.integration.modules.NEIHelpers.NEIFacadeRecipeHandler::AEFacadeCrafting` | Display | 6728 | 6728 | 0 |
| Inscriber<br>`appeng.integration.modules.NEIHelpers.NEIInscriberRecipeHandler::inscriber` | Display | 12 | 12 | 0 |
| Encoded Pattern<br>`appeng.integration.modules.NEIHelpers.NEIPatternViewHandler::` | Unavailable | — | — | — |
| AE2 In-World Crafting<br>`appeng.integration.modules.NEIHelpers.NEIWorldCraftingHandler::` | No enumeration | 0 | 0 | 0 |
| Bio Lab<br>`bartworks.neiHandler.BioLabNEIHandler::bw.recipe.biolab` | Native | — | — | — |
| Bacterial Vat<br>`bartworks.neiHandler.BioVatNEIHandler::bw.recipe.BacteriaVat` | Native | — | — | — |
| Lumbermill<br>`binnie.extratrees.nei.NEIHandlerLumbermill::extratrees.lumbermill` | Archived/info | 104 | 0 | 104 |
| Acclimatiser<br>`binnie.genetics.nei.AcclimatiserRecipeHandler::genetics.acclimatiser` | Archived/info | 12 | 0 | 12 |
| Analyzer<br>`binnie.genetics.nei.AnalyserRecipeHandler::genetics.analyser` | Archived/info | 13 | 0 | 13 |
| Gene Database<br>`binnie.genetics.nei.DatabaseRecipeHandler::genetics.database` | Display | 2 | 2 | 0 |
| Genepool<br>`binnie.genetics.nei.GenepoolRecipeHandler::genetics.genepool` | Archived/info | 12 | 0 | 12 |
| Incubator<br>`binnie.genetics.nei.IncubatorRecipeHandler::genetics.incubator` | Display | 160 | 153 | 7 |
| Inoculator<br>`binnie.genetics.nei.InoculatorRecipeHandler::genetics.inoculator` | Display | 8 | 8 | 0 |
| Isolator<br>`binnie.genetics.nei.IsolatorRecipeHandler::genetics.isolator` | Display | 12 | 12 | 0 |
| Polymeriser<br>`binnie.genetics.nei.PolymeriserRecipeHandler::genetics.polymeriser` | Unavailable | — | — | — |
| Sequencer<br>`binnie.genetics.nei.SequencerRecipeHandler::genetics.sequencer` | Archived/info | 1 | 0 | 1 |
| Splicer<br>`binnie.genetics.nei.SplicerRecipeHandler::genetics.splicer` | Display | 8 | 8 | 0 |
| blockrenderer6343.integration.gregtech.GTNEIMultiblockHandler<br>`blockrenderer6343.integration.gregtech.GTNEIMultiblockHandler::blockrenderer6343.integration.gregtech.GTNEIMultiblockHandler` | Archived/info | 334 | 0 | 334 |
| blockrenderer6343.integration.structurelib.StructureCompatNEIHandler<br>`blockrenderer6343.integration.structurelib.StructureCompatNEIHandler::blockrenderer6343.integration.structurelib.StructureCompatNEIHandler` | Archived/info | 15 | 0 | 15 |
| BetterQuesting<br>`bq_standard.integration.nei.QuestRecipeHandler::bq_quest` | No enumeration | 0 | 0 | 0 |
| Assembly Table<br>`buildcraft.compat.nei.RecipeHandlerAssemblyTable::` | Display | 45 | 45 | 0 |
| Integration Table<br>`buildcraft.compat.nei.RecipeHandlerIntegrationTable::` | Display | 2 | 2 | 0 |
| Refinery<br>`buildcraft.compat.nei.RecipeHandlerRefinery::` | Archived/info | 2 | 0 | 2 |
| Brewing<br>`codechicken.nei.recipe.BrewingRecipeHandler::brewing` | Display | 194 | 194 | 0 |
| Fireworks<br>`codechicken.nei.recipe.FireworkRecipeHandler::crafting` | Display | 357 | 357 | 0 |
| Fuel<br>`codechicken.nei.recipe.FuelRecipeHandler::fuel` | No enumeration | 0 | 0 | 0 |
| Smelting<br>`codechicken.nei.recipe.FurnaceRecipeHandler::smelting` | Native | — | — | — |
| Information<br>`codechicken.nei.recipe.InformationHandler::information` | No enumeration | 0 | 0 | 0 |
| Crafting Profiling<br>`codechicken.nei.recipe.ProfilerRecipeHandler::` | No enumeration | 0 | 0 | 0 |
| Repair<br>`codechicken.nei.recipe.RepairRecipeHandler::repair` | No enumeration | 0 | 0 | 0 |
| Shaped Crafting<br>`codechicken.nei.recipe.ShapedRecipeHandler::crafting` | Native | — | — | — |
| Shapeless Crafting<br>`codechicken.nei.recipe.ShapelessRecipeHandler::crafting` | Native | — | — | — |
| Reactor<br>`com.brandon3055.draconicevolution.integration.nei.ReactorNEIHandler::draconicevolution.reactor` | Display | 1 | 1 | 0 |
| Shapeless Crafting<br>`com.bymarcin.openglasses.integration.nei.recipe.RecipeHandlerOpenGlassesChatBoxUpgrade::crafting` | No enumeration | 0 | 0 | 0 |
| IRecipeInfo<br>`com.creativemd.creativecore.api.nei.NEIRecipeInfoHandler::crafting` | No enumeration | 0 | 0 | 0 |
| Vending Machine<br>`com.cubefury.vendingmachine.integration.nei.NeiRecipeHandler::vendingmachine` | No enumeration | 0 | 0 | 0 |
| Crop Breeder<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.CropsNHNEICropBreederHandler::cropsnh.recipes.cropBreeder` | Archived/info | 172 | 0 | 172 |
| Crop Gene Extractor<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.CropsNHNEICropGeneExtractorHandler::cropsnh.recipes.cropGeneExtractor` | Archived/info | 716 | 0 | 716 |
| Crop Synthesizer<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.CropsNHNEICropSynthesizerHandler::cropsnh.recipes.cropSynthesizer` | Archived/info | 179 | 0 | 179 |
| Seed Generator<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.CropsNHNEISeedGeneratorHandler::cropsnh.recipes.seedGenerator` | Archived/info | 223 | 0 | 223 |
| Alternate Seeds<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.NEICropsNHAlternateSeedHandler::cropsnhAlternateSeeds` | Display | 127 | 127 | 0 |
| Crops<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.NEICropsNHCropHandler::cropsnhCrops` | Archived/info | 179 | 0 | 179 |
| Crop Breeding<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.NEICropsNHCropstickBreedingHandler::cropsnhBreeding` | Display | 153 | 153 | 0 |
| Crop Mutation Pools<br>`com.gtnewhorizon.cropsnh.compatibility.NEI.NEICropsNHMutationPoolHandler::mutationPool` | Archived/info | 66 | 0 | 66 |
| Alchemy<br>`com.gtnewhorizons.aspectrecipeindex.nei.AlchemyRecipeHandler::thaumcraft.alchemy` | Display | 777 | 777 | 0 |
| Aspect Combination<br>`com.gtnewhorizons.aspectrecipeindex.nei.AspectCombinationHandler::thaumcraft.aspects` | Display | 6 | 6 | 0 |
| Arcane Infusion<br>`com.gtnewhorizons.aspectrecipeindex.nei.InfusionRecipeHandler::thaumcraft.infusion` | Unavailable | — | — | — |
| Items Containing Aspect<br>`com.gtnewhorizons.aspectrecipeindex.nei.ItemsContainingAspectHandler::thaumcraft.items_containing_aspect` | Display | 6 | 6 | 0 |
| Shaped Arcane Crafting<br>`com.gtnewhorizons.aspectrecipeindex.nei.arcaneworkbench.ShapedArcaneRecipeHandler::thaumcraft.arcane.shaped` | Display | 586 | 586 | 0 |
| Shapeless Arcane Crafting<br>`com.gtnewhorizons.aspectrecipeindex.nei.arcaneworkbench.ShapelessArcaneRecipeHandler::thaumcraft.arcane.shapeless` | Display | 41 | 41 | 0 |
| Wand Crafting<br>`com.gtnewhorizons.aspectrecipeindex.nei.arcaneworkbench.WandRecipeHandler::thaumcraft.wands` | Display | 1600 | 1600 | 0 |
| Mob Info<br>`com.kuba6000.mobsinfo.nei.MobHandler::mobsinfo.mobhandler` | No enumeration | 0 | 0 | 0 |
| Fishing Loot Table<br>`com.kuba6000.mobsinfo.nei.MobHandlerFishing::mobsinfo.mobhandlerfishing` | No enumeration | 0 | 0 | 0 |
| Infernal Drops<br>`com.kuba6000.mobsinfo.nei.MobHandlerInfernal::mobsinfo.mobhandlerinfernal` | No enumeration | 0 | 0 | 0 |
| Villager Trades<br>`com.kuba6000.mobsinfo.nei.VillagerTradesHandler::mobsinfo.villagertradeshandler` | No enumeration | 0 | 0 | 0 |
| Animal Trap Drops<br>`com.pam.harvestcraft.AnimalTrapRecipeHandler::harvestcraftAnimalTrapDrops` | Archived/info | 3 | 0 | 3 |
| Fish Trap Drops<br>`com.pam.harvestcraft.FishTrapRecipeHandler::harvestcraftFishTrapDrops` | Archived/info | 1 | 0 | 1 |
| Garden Drops<br>`com.pam.harvestcraft.GardenRecipeHandler::harvestcraftGardenDrops` | Archived/info | 12 | 0 | 12 |
| Manipulator Upgrades<br>`com.recursive_pineapple.matter_manipulator.client.nei.UpdateRecipeNEIHandler::mm.recipe.upgrades` | Display | 9 | 9 | 0 |
| QED Recipes<br>`com.rwtema.extrautils.nei.EnderConstructorHandler::qedcrafting` | Display | 7 | 7 | 0 |
| Microblock Crafting<br>`com.rwtema.extrautils.nei.FMPMicroBlocksHandler::crafting` | Unavailable | — | — | — |
| Documentation<br>`com.rwtema.extrautils.nei.InfoHandler::` | No enumeration | 0 | 0 | 0 |
| Extra Utilities: Microblocks<br>`com.rwtema.extrautils.nei.MicroBlocksHandler::crafting` | Display | 4 | 4 | 0 |
| Soul Crafting<br>`com.rwtema.extrautils.nei.SoulHandler::` | No enumeration | 0 | 0 | 0 |
| Alloy Smelter<br>`crazypants.enderio.nei.AlloySmelterRecipeHandler::EnderIOAlloySmelter` | Display | 11 | 11 | 0 |
| Enchanter<br>`crazypants.enderio.nei.EnchanterRecipeHandler::EIOEnchanter` | Display | 28 | 28 | 0 |
| SAG Mill<br>`crazypants.enderio.nei.SagMillRecipeHandler::EnderIOSagMill` | Display | 1663 | 1663 | 0 |
| Slice'N'Splice<br>`crazypants.enderio.nei.SliceAndSpliceRecipeHandler::EnderIOSliceAndSplice` | Display | 7 | 7 | 0 |
| Soul Binder<br>`crazypants.enderio.nei.SoulBinderRecipeHandler::EnderIOSoulBinder` | Display | 8 | 8 | 0 |
| Vat<br>`crazypants.enderio.nei.VatRecipeHandler::EnderIOVat` | Archived/info | 8 | 0 | 8 |
| Circuit Fabricator<br>`de.katzenpapst.amunra.nei.recipehandler.ARCircuitFab::` | No enumeration | 0 | 0 | 0 |
| NASA Workbench<br>`de.katzenpapst.amunra.nei.recipehandler.ARNasaWorkbenchShuttle::` | No enumeration | 0 | 0 | 0 |
| Wand Cap Substitution<br>`dev.rndmorris.salisarcana.common.compat.nei.WandCapSubstitutionHandler::salisarcana.substitution.caps` | Display | 80 | 80 | 0 |
| Wand Core Substitution<br>`dev.rndmorris.salisarcana.common.compat.nei.WandCoreSubstitutionHandler::salisarcana.substitution.core` | Display | 80 | 80 | 0 |
| LootBag<br>`eu.usrv.enhancedlootbags.integration.nei.LootBagRecipeHandler::enhancedlootbags` | Unavailable | — | — | — |
| Thaumic Replication<br>`flaxbeard.thaumicexploration.integration.nei.ReplicatorHandler::ThaumicExploration.replicator` | Archived/info | 101 | 0 | 101 |
| Bottler<br>`forestry.factory.recipes.nei.NEIHandlerBottler::forestry.bottler` | Display | 1556 | 1556 | 0 |
| Carpenter<br>`forestry.factory.recipes.nei.NEIHandlerCarpenter::forestry.carpenter` | Display | 418 | 418 | 0 |
| Centrifuge<br>`forestry.factory.recipes.nei.NEIHandlerCentrifuge::forestry.centrifuge` | Archived/info | 154 | 0 | 154 |
| Thermionic Fabricator<br>`forestry.factory.recipes.nei.NEIHandlerFabricator::forestry.fabricator` | Display | 93 | 93 | 0 |
| Fermenter<br>`forestry.factory.recipes.nei.NEIHandlerFermenter::forestry.fermenter` | Archived/info | 707 | 0 | 707 |
| Moistener<br>`forestry.factory.recipes.nei.NEIHandlerMoistener::forestry.moistener` | Display | 49 | 49 | 0 |
| Squeezer<br>`forestry.factory.recipes.nei.NEIHandlerSqueezer::forestry.squeezer` | Display | 282 | 130 | 152 |
| Still<br>`forestry.factory.recipes.nei.NEIHandlerStill::forestry.still` | Archived/info | 3 | 0 | 3 |
| Neutronium Compressor<br>`fox.spiteful.avaritia.compat.nei.CompressionHandler::extreme_compression` | No enumeration | 0 | 0 | 0 |
| Xtreme Crafting<br>`fox.spiteful.avaritia.compat.nei.ExtremeShapedRecipeHandler::extreme` | Display | 135 | 135 | 0 |
| Xtreme Shapeless<br>`fox.spiteful.avaritia.compat.nei.ExtremeShapelessRecipeHandler::extreme` | Display | 3 | 3 | 0 |
| Assembly Machine<br>`galaxyspace.core.nei.AssemblyMachineRecipeHandler::` | No enumeration | 0 | 0 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT1` | Display | 4 | 4 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT2` | Display | 4 | 4 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT3` | Display | 4 | 4 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT4` | Display | 4 | 4 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT5` | Display | 4 | 4 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT6` | Display | 4 | 4 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT7` | Display | 4 | 4 | 0 |
| §9NASA Workbench<br>`galaxyspace.core.nei.RocketRecipeHandler::galacticraft.rocketT8` | Display | 4 | 4 | 0 |
| Banners<br>`ganymedes01.etfuturum.compat.nei.BannerPatternHandler::crafting` | Display | 44 | 44 | 0 |
| Blasting<br>`ganymedes01.etfuturum.compat.nei.BlastFurnaceRecipeHandler::etfuturum.blastfurnace` | No enumeration | 0 | 0 | 0 |
| Smoking<br>`ganymedes01.etfuturum.compat.nei.SmokerRecipeHandler::etfuturum.smoker` | No enumeration | 0 | 0 | 0 |
| Shaped Crafting<br>`gcewing.projectblue.nei.NEIRecipeHandler::crafting` | No enumeration | 0 | 0 | 0 |
| Acid Generator<br>`gregtech.nei.GTNEIDefaultHandler::bw.fuels.acidgens` | Native | — | — | — |
| Circuit Assembly Line<br>`gregtech.nei.GTNEIDefaultHandler::bw.recipe.cal` | Native | — | — | — |
| Radio Hatch Material List<br>`gregtech.nei.GTNEIDefaultHandler::bw.recipe.radhatch` | Native | — | — | — |
| Component Assembly Line<br>`gregtech.nei.GTNEIDefaultHandler::gg.recipe.componentassemblyline` | Native | — | — | — |
| Extreme Heat Exchanger<br>`gregtech.nei.GTNEIDefaultHandler::gg.recipe.extreme_heat_exchanger` | Native | — | — | — |
| Naquadah Fuel Refinery<br>`gregtech.nei.GTNEIDefaultHandler::gg.recipe.naquadah_fuel_refine_factory` | Native | — | — | — |
| Large Naquadah Reactor<br>`gregtech.nei.GTNEIDefaultHandler::gg.recipe.naquadah_reactor` | Native | — | — | — |
| Neutron Activator<br>`gregtech.nei.GTNEIDefaultHandler::gg.recipe.neutron_activator` | Native | — | — | — |
| Precise Assembler<br>`gregtech.nei.GTNEIDefaultHandler::gg.recipe.precise_assembler` | Native | — | — | — |
| RTG<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.RTGgenerators` | Native | — | — | — |
| Algae Pond<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.algae_pond` | Native | — | — | — |
| Alloy Blast Smelter<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.alloyblastsmelter` | Native | — | — | — |
| Alloy Smelter<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.alloysmelter` | Native | — | — | — |
| Arc Furnace<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.arcfurnace` | Native | — | — | — |
| Condensate Assembling<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.assemble-condensate` | Native | — | — | — |
| Assembler<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.assembler` | Native | — | — | — |
| Autoclave<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.autoclave` | Native | — | — | — |
| Beam Crafter<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.beamcrafter` | Native | — | — | — |
| Blast Furnace<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.blastfurnace` | Native | — | — | — |
| Brewery<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.brewer` | Native | — | — | — |
| Cable Coating<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.cable` | Native | — | — | — |
| Canner<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.canner` | Native | — | — | — |
| ABS Non-Alloy Recipes<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.abs_non_alloy_recipes` | Native | — | — | — |
| Alloy Smelter Molding<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.alloy_smelter_molding` | Native | — | — | — |
| Alloy Smelter Recycling<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.alloy_smelter_recycling` | Native | — | — | — |
| Arc Furnace Recycling<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.arc_furnace_recycling` | Native | — | — | — |
| Fluid Extractor Recycling<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.fluid_extractor_recycling` | Native | — | — | — |
| Forge Hammer Recycling<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.forge_hammer_recycling` | Native | — | — | — |
| Macerator Recycling<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.macerator_recycling` | Native | — | — | — |
| TiC Bolt Molding<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.tic_bolt_molding` | Native | — | — | — |
| TiC Part Extruding<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.category.tic_part_extruding` | Native | — | — | — |
| Cauldron<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.cauldron` | Native | — | — | — |
| Centrifuge<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.centrifuge` | Native | — | — | — |
| Chemical Bath<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.chemicalbath` | Native | — | — | — |
| Dehydrator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.chemicaldehydrator` | Native | — | — | — |
| Chemical Reactor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.chemicalreactor` | Native | — | — | — |
| Circuit Assembler<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.circuitassembler` | Native | — | — | — |
| Coke Oven<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.cokeoven` | Native | — | — | — |
| Cold Trap<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.coldtrap` | Native | — | — | — |
| Compressor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.compressor` | Native | — | — | — |
| Oil Cracker<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.craker` | Native | — | — | — |
| Condensate Entanglement<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.create-condensate` | Native | — | — | — |
| COMET - Compact Cyclotron<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.cyclotron` | Native | — | — | — |
| Combustion Generator Fue...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.dieselgeneratorfuel` | Native | — | — | — |
| Distillation Tower<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.distillationtower` | Native | — | — | — |
| Distillery<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.distillery` | Native | — | — | — |
| Electric Implosion Compres...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.electricimplosioncompressor` | Native | — | — | — |
| Electrolyzer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.electrolyzer` | Native | — | — | — |
| Electromagnetic Separator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.electromagneticseparator` | Native | — | — | — |
| Entropic Processing<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.entropic-processing` | Native | — | — | — |
| Extractor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.extractor` | Native | — | — | — |
| Naquadah Reactor Mk-V<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.extrahugenaquadahreactor` | Native | — | — | — |
| Extreme Diesel Engine Fuel<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.extremedieselgeneratorfuel` | Native | — | — | — |
| Extruder<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.extruder` | Native | — | — | — |
| Eye of Harmony<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.eyeofharmony` | Native | — | — | — |
| Assemblyline Process<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fakeAssemblylineProcess` | Native | — | — | — |
| Cutting Machine<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fakecuttingsaw` | Native | — | — | — |
| Space Projects<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fakespaceprojects` | Native | — | — | — |
| Fermenter<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fermenter` | Native | — | — | — |
| Zhuhai Fishing Port<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fishpond` | Native | — | — | — |
| Nuclear Fuel Processing<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fissionfuel` | Native | — | — | — |
| Flotation Cell<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.flotationcell` | Native | — | — | — |
| Chemical Plant<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fluidchemicaleactor` | Native | — | — | — |
| Fluid Extractor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fluidextractor` | Native | — | — | — |
| Fluid Heater<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fluidheater` | Native | — | — | — |
| Naquadah Reactor Mk-III<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fluidnaquadahreactor` | Native | — | — | — |
| Fluid Solidifier<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fluidsolidifier` | Native | — | — | — |
| Heliofusion Exoticizer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fog_exotic` | Native | — | — | — |
| Helioflux Melting Core<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fog_molten` | Native | — | — | — |
| Heliothermal Plasma Fabric...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fog_plasma` | Native | — | — | — |
| Foundry Module Componen...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.foundry_modules` | Native | — | — | — |
| Fusion Reactor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.fusionreactor` | Native | — | — | — |
| Gas Turbine Fuel<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.gasturbinefuel` | Native | — | — | — |
| Forge Hammer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.hammer` | Native | — | — | — |
| Naquadah Reactor Mk-IV<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.hugenaquadahreactor` | Native | — | — | — |
| Nuclear Fission<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.ic2nuke` | Native | — | — | — |
| Ice Cream Machine<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.icecreammachine` | Native | — | — | — |
| Implosion Compressor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.implosioncompressor` | Native | — | — | — |
| Industrial Chisel<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.industrialchisel` | Native | — | — | — |
| Industrial Coke Oven<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.industrialcokeoven` | Native | — | — | — |
| Isotope Decay<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.isotope-decay` | Native | — | — | — |
| Large Hadron Collider<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.large_hadron_collider` | Native | — | — | — |
| Large Boiler<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.largeboilerfakefuels` | Native | — | — | — |
| Large Chemical Reactor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.largechemicalreactor` | Native | — | — | — |
| Naquadah Reactor Mk-II<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.largenaquadahreactor` | Native | — | — | — |
| Laser Engraver<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.laserengraver` | Native | — | — | — |
| Lathe<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.lathe` | Native | — | — | — |
| Liquid Fluoride Thorium Re...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.lftr` | Native | — | — | — |
| LFTR Gas Sparging<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.lftr.sparging` | Native | — | — | — |
| Macerator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.macerator` | Native | — | — | — |
| Magic Energy Converter F...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.magicfuels` | Native | — | — | — |
| Mass Fabrication<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.massfab` | Native | — | — | — |
| Matter Fabricator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.matterfab2` | Native | — | — | — |
| Bending Machine<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.metalbender` | Native | — | — | — |
| Mixer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.mixer` | Native | — | — | — |
| Molecular Transformer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.moleculartransformer` | Native | — | — | — |
| Boldarnator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.multiblockrockbreaker` | Native | — | — | — |
| Multiblock Centrifuge<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.multicentrifuge` | Native | — | — | — |
| Multiblock Dehydrator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.multidehydrator` | Native | — | — | — |
| Multiblock Electrolyzer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.multielectro` | Native | — | — | — |
| Multiblock Mixer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.multimixer` | Native | — | — | — |
| Nanochip Assembly Matrix<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.assemblymatrix` | Native | — | — | — |
| Accelerated Biological Coo...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.biologicalcoordinator` | Native | — | — | — |
| Full-Board Immersion Devi...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.boardprocessor` | Native | — | — | — |
| Nanochip Conversion Comp...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.conversion` | Native | — | — | — |
| Nanoprecision Cutting Cha...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.cuttingchamber` | Native | — | — | — |
| Nanometer Encasement Wr...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.encasementwrapper` | Native | — | — | — |
| Ultra-high Energy Etching...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.etchingarray` | Native | — | — | — |
| Optically Optimized Organiz...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.opticalorganizer` | Native | — | — | — |
| Part Processing Apparatus<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.partprocessor` | Native | — | — | — |
| Superconductive Strand S...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.superconductorsplitter` | Native | — | — | — |
| Nanoprecision Wire Tracer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanochip.wiretracer` | Native | — | — | — |
| Nano Forge<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nanoforge` | Native | — | — | — |
| Neutronium Compressor<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.neutroniumcompressor` | Native | — | — | — |
| Nuclear Salt Processing P...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.nuclearsaltprocessingplant` | Native | — | — | — |
| Milling<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.oremill` | Native | — | — | — |
| Ore Washer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.orewasher` | Native | — | — | — |
| Packager<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.packager` | Native | — | — | — |
| PCB Factory<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.pcbfactory` | Native | — | — | — |
| DTPF<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.plasmaforge` | Native | — | — | — |
| Plasma Generator Fuels<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.plasmageneratorfuels` | Native | — | — | — |
| Electromagnetic Polarizer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.polarizer` | Native | — | — | — |
| Forming Press<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.press` | Native | — | — | — |
| Bricked Blast Furnace<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.primitiveblastfurnace` | Native | — | — | — |
| Printer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.printer` | Native | — | — | — |
| Clarifier<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantclarifier` | Native | — | — | — |
| Degassing<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantdegasifier` | Native | — | — | — |
| Flocculation<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantflocculation` | Native | — | — | — |
| Ozonation<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantozonation` | Native | — | — | — |
| pH Neutralization<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantphadjustment` | Native | — | — | — |
| Temperature Fluctuation<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantplasmaheating` | Native | — | — | — |
| Baryonic Perfection<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantquarkextractor` | Native | — | — | — |
| High Energy Laser Treatm...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.purificationplantuvtreatment` | Native | — | — | — |
| Pyrolyse Oven<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.pyro` | Native | — | — | — |
| QC Components<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.quantumcomputer` | Native | — | — | — |
| Quantum Force Transform...<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.quantumforcesmelter` | Native | — | — | — |
| Reactor Processing Unit<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.reactorprocessingunit` | Native | — | — | — |
| Replicator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.replicator` | Native | — | — | — |
| Research Station<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.researchStation` | Native | — | — | — |
| Rock Breaker<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.rockbreaker` | Native | — | — | — |
| Rocket Engine Fuel<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.rocketenginefuel` | Native | — | — | — |
| Scanner<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.scanner` | Native | — | — | — |
| Semifluid Generator Fuels<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.semifluidgeneratorfuels` | Native | — | — | — |
| Sifter<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.sifter` | Native | — | — | — |
| Simple Dust Washer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.simplewasher` | Native | — | — | — |
| Naquadah Reactor Mk-I<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.smallnaquadahreactor` | Native | — | — | — |
| Solar Factory<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.solarfactory` | Native | — | — | — |
| Solar Tower<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.solartower` | Native | — | — | — |
| Space Assembler<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.spaceAssembler` | Native | — | — | — |
| Space Mining<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.spaceMining` | Native | — | — | — |
| Space Research<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.spaceResearch` | Native | — | — | — |
| Thermal Boiler<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.thermalboiler` | Native | — | — | — |
| Thermal Centrifuge<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.thermalcentrifuge` | Native | — | — | — |
| Thermal Generator Fuels<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.thermalgeneratorfuel` | Native | — | — | — |
| Transcendent Plasma Mixer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.transcendentplasmamixerrecipes` | Native | — | — | — |
| Tree Growth Simulator<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.treefarm` | Native | — | — | — |
| Unpackager<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.unpackager` | Native | — | — | — |
| Godforge Upgrades<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.upgrade_costs` | Native | — | — | — |
| Matter Amplifier<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.uuamplifier` | Native | — | — | — |
| Vacuum Furnace<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.vacfurnace` | Native | — | — | — |
| Vacuum Freezer<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.vacuumfreezer` | Native | — | — | — |
| Wiremill<br>`gregtech.nei.GTNEIDefaultHandler::gt.recipe.wiremill` | Native | — | — | — |
| Digester<br>`gregtech.nei.GTNEIDefaultHandler::gtnhlanth.recipe.digester` | Native | — | — | — |
| Dissolution Tank<br>`gregtech.nei.GTNEIDefaultHandler::gtnhlanth.recipe.disstank` | Native | — | — | — |
| Source Chamber<br>`gregtech.nei.GTNEIDefaultHandler::gtnhlanth.recipe.sc` | Native | — | — | — |
| Target Chamber<br>`gregtech.nei.GTNEIDefaultHandler::gtnhlanth.recipe.tc` | Native | — | — | — |
| Draconic Evolution Fusion ...<br>`gregtech.nei.GTNEIDefaultHandler::kubatech.defusioncrafter` | Native | — | — | — |
| High Temperature Gas Rea...<br>`gregtech.nei.GTNEIDefaultHandler::kubatech.htgrrecipes` | Native | — | — | — |
| Circuit Assembly Line Imprinting<br>`gregtech.nei.GTNEIImprintHandler::gt.recipe.cal-imprinting` | Display | 37 | 37 | 0 |
| Macerator Conversion<br>`gregtech.nei.GTNEIMacerationStackConversion::gt.recipe.macerator.conversion` | Display | 2 | 2 | 0 |
| Decayables<br>`gtPlusPlus.nei.DecayableRecipeHandler::GTPP_Decayables` | Display | 4 | 4 | 0 |
| Small Ore Stats<br>`gtneioreplugin.plugin.gregtech5.PluginGT5SmallOreStat::` | Archived/info | 54 | 0 | 54 |
| Underground Fluid Stats<br>`gtneioreplugin.plugin.gregtech5.PluginGT5UndergroundFluid::` | Display | 30 | 30 | 0 |
| Vein Stats<br>`gtneioreplugin.plugin.gregtech5.PluginGT5VeinStat::` | Archived/info | 122 | 0 | 122 |
| Planetary Gas Siphon<br>`gtnhintergalactic.nei.GasSiphonRecipeHandler::` | Display | 16 | 16 | 0 |
| Space Pumping Module<br>`gtnhintergalactic.nei.SpacePumpModuleRecipeHandler::` | Display | 40 | 40 | 0 |
| Bee Breeding Tree<br>`hellfirepvp.beebetteratbees.client.gui.BBABGuiRecipeTreeHandler::` | Display | 428 | 428 | 0 |
| Shaped IC2 Crafting<br>`ic2.neiIntegration.core.recipehandler.AdvRecipeHandler::crafting` | Native | — | — | — |
| Shapeless IC2 Crafting<br>`ic2.neiIntegration.core.recipehandler.AdvShapelessRecipeHandler::crafting` | Native | — | — | — |
| Blast Furnace<br>`ic2.neiIntegration.core.recipehandler.BlastFurnaceRecipeHandler::blastfurnace` | No enumeration | 0 | 0 | 0 |
| Block Cutting Machine<br>`ic2.neiIntegration.core.recipehandler.BlockCutterRecipeHandler::BlockCutter` | No enumeration | 0 | 0 | 0 |
| Thermal Centrifuge<br>`ic2.neiIntegration.core.recipehandler.CentrifugeRecipeHandler::centrifuge` | No enumeration | 0 | 0 | 0 |
| Compressor<br>`ic2.neiIntegration.core.recipehandler.CompressorRecipeHandler::compressor` | No enumeration | 0 | 0 | 0 |
| Extractor<br>`ic2.neiIntegration.core.recipehandler.ExtractorRecipeHandler::extractor` | No enumeration | 0 | 0 | 0 |
| Fluid Canning Machine<br>`ic2.neiIntegration.core.recipehandler.FluidCannerRecipeHandler::fluidcanner` | Display | 5 | 5 | 0 |
| Turning Table<br>`ic2.neiIntegration.core.recipehandler.LatheRecipeHandler::` | No enumeration | 0 | 0 | 0 |
| Macerator<br>`ic2.neiIntegration.core.recipehandler.MaceratorRecipeHandler::macerator` | No enumeration | 0 | 0 | 0 |
| Metal Former<br>`ic2.neiIntegration.core.recipehandler.MetalFormerRecipeHandlerCutting::metalformer` | Display | 1 | 1 | 0 |
| Metal Former<br>`ic2.neiIntegration.core.recipehandler.MetalFormerRecipeHandlerExtruding::metalformer` | No enumeration | 0 | 0 | 0 |
| Metal Former<br>`ic2.neiIntegration.core.recipehandler.MetalFormerRecipeHandlerRolling::metalformer` | No enumeration | 0 | 0 | 0 |
| Ore Washing Plant<br>`ic2.neiIntegration.core.recipehandler.OreWashingRecipeHandler::oreWashing` | No enumeration | 0 | 0 | 0 |
| Scrapbox<br>`ic2.neiIntegration.core.recipehandler.ScrapboxRecipeHandler::` | Display | 80 | 80 | 0 |
| Canning Machine<br>`ic2.neiIntegration.core.recipehandler.SolidCannerRecipeHandler::solidcanner` | Display | 20 | 20 | 0 |
| OpenComputers API<br>`li.cil.oc.integration.nei.CallbackDocHandler::` | No enumeration | 0 | 0 | 0 |
| Manual<br>`li.cil.oc.integration.nei.ManualUsageHandler::` | No enumeration | 0 | 0 | 0 |
| Soldering Station<br>`logisticspipes.nei.NEISolderingStationRecipeManager::crafting` | No enumeration | 0 | 0 | 0 |
| Imbuing Station<br>`lumien.randomthings.Handler.ModCompability.NEI.ImbuingStationRecipeHandler::imbuing` | Display | 7 | 7 | 0 |
| §9NASA Workbench<br>`micdoodle8.mods.galacticraft.core.nei.BuggyRecipeHandler::galacticraft.buggy` | Display | 4 | 4 | 0 |
| Circuit Fabricator<br>`micdoodle8.mods.galacticraft.core.nei.CircuitFabricatorRecipeHandler::` | Archived/info | 3 | 0 | 3 |
| Refinery<br>`micdoodle8.mods.galacticraft.core.nei.RefineryRecipeHandler::` | Display | 1 | 1 | 0 |
| §9NASA Workbench<br>`micdoodle8.mods.galacticraft.planets.asteroids.nei.AstroMinerRecipeHandler::galacticraft.astroMiner` | Display | 1 | 1 | 0 |
| §9NASA Workbench<br>`micdoodle8.mods.galacticraft.planets.mars.nei.CargoRocketRecipeHandler::galacticraft.cargoRocket` | Display | 3 | 3 | 0 |
| Shaped Crafting<br>`mrtjp.projectred.core.libmc.recipe.PRShapedRecipeHandler::crafting` | No enumeration | 0 | 0 | 0 |
| Shapeless Crafting<br>`mrtjp.projectred.core.libmc.recipe.PRShapelessRecipeHandler::crafting` | No enumeration | 0 | 0 | 0 |
| DNA Extractor<br>`net.bdew.gendustry.nei.ExtractorHandler::` | Archived/info | 1593 | 0 | 1593 |
| Genetic Imprinter<br>`net.bdew.gendustry.nei.ImprinterHandler::` | Display | 1 | 1 | 0 |
| Protein Liquifier<br>`net.bdew.gendustry.nei.LiquifierHandler::` | Archived/info | 40 | 0 | 40 |
| Mutagen Producer<br>`net.bdew.gendustry.nei.MutagenProducerHandler::` | Archived/info | 15 | 0 | 15 |
| Mutatron<br>`net.bdew.gendustry.nei.MutatronHandler::` | Display | 713 | 713 | 0 |
| Genetic Replicator<br>`net.bdew.gendustry.nei.ReplicatorHandler::` | Display | 3 | 3 | 0 |
| Genetic Sampler<br>`net.bdew.gendustry.nei.SamplerHandler::` | Display | 9281 | 9281 | 0 |
| Genetic Template<br>`net.bdew.gendustry.nei.TemplateCraftingHandler::crafting` | No enumeration | 0 | 0 | 0 |
| Genetic Transposer<br>`net.bdew.gendustry.nei.TransposerHandler::` | Display | 8 | 8 | 0 |
| Flower Breeding<br>`net.bdew.neiaddons.botany.flowers.FlowerBreedingHandler::` | Display | 37 | 37 | 0 |
| Bee Breeding<br>`net.bdew.neiaddons.forestry.bees.BeeBreedingHandler::` | Display | 543 | 543 | 0 |
| Bee Produce<br>`net.bdew.neiaddons.forestry.bees.BeeProduceHandler::` | Display | 453 | 453 | 0 |
| Butterfly Breeding<br>`net.bdew.neiaddons.forestry.butterflies.ButterflyBreedingHandler::` | No enumeration | 0 | 0 | 0 |
| Tree Breeding<br>`net.bdew.neiaddons.forestry.trees.TreeBreedingHandler::` | Display | 133 | 133 | 0 |
| Tree Produce<br>`net.bdew.neiaddons.forestry.trees.TreeProduceHandler::` | Display | 103 | 103 | 0 |
| Shaped Crafting<br>`net.p455w0rd.wirelesscraftingterminal.integration.modules.NEIHelpers.NEIAEShapedRecipeHandler::crafting` | Native | — | — | — |
| Smeltery Alloying<br>`tconstruct.plugins.nei.RecipeHandlerAlloying::` | Archived/info | 8 | 0 | 8 |
| Casting Basin<br>`tconstruct.plugins.nei.RecipeHandlerCastingBasin::` | Display | 16 | 16 | 0 |
| Casting Table<br>`tconstruct.plugins.nei.RecipeHandlerCastingTable::` | Display | 1088 | 1088 | 0 |
| Drying Rack<br>`tconstruct.plugins.nei.RecipeHandlerDryingRack::` | Display | 10 | 10 | 0 |
| Smeltery Melting<br>`tconstruct.plugins.nei.RecipeHandlerMelting::` | Archived/info | 719 | 0 | 719 |
| Tool Materials<br>`tconstruct.plugins.nei.RecipeHandlerToolMaterials::` | Archived/info | 364 | 0 | 364 |
| Chisel<br>`team.chisel.compat.nei.RecipeHandlerChisel::` | Archived/info | 307 | 0 | 307 |
| Infused Seed Drops<br>`thaumic.tinkerer.client.nei.NEIInfusedSeedRecipeHandler::thaumictinkerer.infusedseeds` | Archived/info | 47 | 0 | 47 |
| Tablet of Necromancy<br>`thaumic.tinkerer.client.nei.NEINecromancyHandler::thaumictinkerer.necromancy` | Display | 30 | 30 | 0 |
| Fluid Registry<br>`tonius.neiintegration.mods.mcforge.RecipeHandlerFluidRegistry::` | No enumeration | 0 | 0 | 0 |
| Ore Dictionary<br>`tonius.neiintegration.mods.mcforge.RecipeHandlerOreDictionary::` | No enumeration | 0 | 0 | 0 |
| Advanced Coke Oven<br>`tonius.neiintegration.mods.railcraft.RecipeHandlerBlastFurnace::` | Display | 163 | 163 | 0 |
| Coke Oven<br>`tonius.neiintegration.mods.railcraft.RecipeHandlerCokeOven::` | Display | 164 | 164 | 0 |
| Rock Crusher<br>`tonius.neiintegration.mods.railcraft.RecipeHandlerRockCrusher::` | No enumeration | 0 | 0 | 0 |
| Rolling Machine<br>`tonius.neiintegration.mods.railcraft.RecipeHandlerRollingMachineShaped::` | Display | 19 | 19 | 0 |
| Rolling Machine<br>`tonius.neiintegration.mods.railcraft.RecipeHandlerRollingMachineShapeless::` | No enumeration | 0 | 0 | 0 |
| Botanical Brewery<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerBrewery::botania.brewery` | Display | 21 | 21 | 0 |
| Elven Trade<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerElvenTrade::botania.elvenTrade` | Display | 11 | 11 | 0 |
| Floating Flowers<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerFloatingFlowers::crafting2x2` | No enumeration | 0 | 0 | 0 |
| Lexica Botania<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerLexicaBotania::botania.lexica` | Display | 449 | 449 | 0 |
| Mana Infusion<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerManaPool::botania.manaPool` | Display | 165 | 165 | 0 |
| Petal Apothecary<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerPetalApothecary::botania.petalApothecary` | Display | 44 | 44 | 0 |
| Pure Daisy<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerPureDaisy::botania.pureDaisy` | Display | 6 | 6 | 0 |
| Runic Altar<br>`vazkii.botania.client.integration.nei.recipe.RecipeHandlerRunicAltar::botania.runicAltar` | Display | 48 | 48 | 0 |
| Infernal Blast Furnace<br>`witchinggadgets.client.nei.NEIInfernalBlastfurnaceHandler::wgInfernalBlastfurnace` | Display | 51 | 51 | 0 |
| Spinning Wheel<br>`witchinggadgets.client.nei.NEISpinningWheelHandler::wgSpinningWheel` | Display | 5 | 5 | 0 |

## Complete GT map inventory

Native and display columns count usable output records; empty and fake-only maps
remain visible in the audit. Research diagrams in the source are excluded from
manufacturing queries as described above.

| Map | Registered | Native exported | Display exported | Archived | Fake | Disabled |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `bw.fuels.acidgens` | 20 | 20 | 0 | 0 | 0 | 0 |
| `bw.recipe.BacteriaVat` | 101 | 101 | 0 | 0 | 0 | 0 |
| `bw.recipe.biolab` | 106 | 51 | 55 | 0 | 55 | 0 |
| `bw.recipe.cal` | 71 | 71 | 0 | 0 | 0 | 0 |
| `bw.recipe.radhatch` | 104 | 0 | 0 | 104 | 104 | 0 |
| `cropsnh.recipes.cropBreeder` | 172 | 172 | 0 | 0 | 0 | 0 |
| `cropsnh.recipes.cropGeneExtractor` | 716 | 0 | 716 | 0 | 716 | 0 |
| `cropsnh.recipes.cropSynthesizer` | 179 | 0 | 179 | 0 | 179 | 0 |
| `cropsnh.recipes.seedGenerator` | 223 | 0 | 223 | 0 | 223 | 0 |
| `gg.recipe.componentassemblyline` | 142 | 142 | 0 | 0 | 0 | 0 |
| `gg.recipe.extreme_heat_exchanger` | 110 | 110 | 0 | 0 | 0 | 0 |
| `gg.recipe.naquadah_fuel_refine_factory` | 9 | 9 | 0 | 0 | 0 | 0 |
| `gg.recipe.naquadah_reactor` | 9 | 9 | 0 | 0 | 0 | 0 |
| `gg.recipe.neutron_activator` | 12 | 12 | 0 | 0 | 0 | 0 |
| `gg.recipe.precise_assembler` | 24 | 24 | 0 | 0 | 0 | 0 |
| `gt.recipe.RTGgenerators` | 5 | 0 | 0 | 5 | 0 | 0 |
| `gt.recipe.algae_pond` | 16 | 16 | 0 | 0 | 0 | 0 |
| `gt.recipe.alloyblastsmelter` | 372 | 372 | 0 | 0 | 0 | 0 |
| `gt.recipe.alloysmelter` | 10356 | 10356 | 0 | 0 | 0 | 0 |
| `gt.recipe.arcfurnace` | 10108 | 10108 | 0 | 0 | 0 | 0 |
| `gt.recipe.assemble-condensate` | 113 | 113 | 0 | 0 | 0 | 0 |
| `gt.recipe.assembler` | 10699 | 10694 | 0 | 5 | 0 | 5 |
| `gt.recipe.autoclave` | 323 | 323 | 0 | 0 | 0 | 0 |
| `gt.recipe.beamcrafter` | 25 | 25 | 0 | 0 | 0 | 0 |
| `gt.recipe.blastfurnace` | 2033 | 2033 | 0 | 0 | 0 | 0 |
| `gt.recipe.brewer` | 442 | 442 | 0 | 0 | 0 | 0 |
| `gt.recipe.cable` | 3297 | 3297 | 0 | 0 | 0 | 0 |
| `gt.recipe.canner` | 4149 | 4149 | 0 | 0 | 0 | 0 |
| `gt.recipe.cauldron` | 860 | 0 | 860 | 0 | 860 | 0 |
| `gt.recipe.centrifuge` | 1392 | 1392 | 0 | 0 | 0 | 0 |
| `gt.recipe.chemicalbath` | 707 | 707 | 0 | 0 | 0 | 0 |
| `gt.recipe.chemicaldehydrator` | 87 | 87 | 0 | 0 | 0 | 0 |
| `gt.recipe.chemicalreactor` | 1285 | 1285 | 0 | 0 | 0 | 0 |
| `gt.recipe.circuitassembler` | 264 | 264 | 0 | 0 | 0 | 0 |
| `gt.recipe.cokeoven` | 8 | 8 | 0 | 0 | 0 | 0 |
| `gt.recipe.coldtrap` | 2 | 2 | 0 | 0 | 0 | 0 |
| `gt.recipe.compressor` | 1241 | 1241 | 0 | 0 | 0 | 0 |
| `gt.recipe.craker` | 85 | 85 | 0 | 0 | 0 | 0 |
| `gt.recipe.create-condensate` | 19 | 19 | 0 | 0 | 0 | 0 |
| `gt.recipe.cuttingsaw` | 9329 | 9329 | 0 | 0 | 0 | 0 |
| `gt.recipe.cyclotron` | 20 | 20 | 0 | 0 | 0 | 0 |
| `gt.recipe.dieselgeneratorfuel` | 20 | 4 | 0 | 16 | 0 | 0 |
| `gt.recipe.distillationtower` | 161 | 161 | 0 | 0 | 0 | 0 |
| `gt.recipe.distillery` | 485 | 485 | 0 | 0 | 0 | 0 |
| `gt.recipe.efrblasting` | 0 | 0 | 0 | 0 | 0 | 0 |
| `gt.recipe.efrsmelting` | 0 | 0 | 0 | 0 | 0 | 0 |
| `gt.recipe.electricimplosioncompressor` | 319 | 319 | 0 | 0 | 0 | 0 |
| `gt.recipe.electrolyzer` | 417 | 417 | 0 | 0 | 0 | 0 |
| `gt.recipe.electromagneticseparator` | 41 | 41 | 0 | 0 | 0 | 0 |
| `gt.recipe.entropic-processing` | 0 | 0 | 0 | 0 | 0 | 0 |
| `gt.recipe.extractor` | 357 | 357 | 0 | 0 | 0 | 0 |
| `gt.recipe.extrahugenaquadahreactor` | 2 | 1 | 0 | 1 | 0 | 0 |
| `gt.recipe.extremedieselgeneratorfuel` | 3 | 0 | 0 | 3 | 0 | 0 |
| `gt.recipe.extruder` | 13457 | 13457 | 0 | 0 | 0 | 0 |
| `gt.recipe.eyeofharmony` | 43 | 43 | 0 | 0 | 0 | 0 |
| `gt.recipe.fakeAssemblylineProcess` | 494 | 0 | 494 | 0 | 494 | 0 |
| `gt.recipe.fakecuttingsaw` | 2562 | 0 | 2562 | 0 | 2562 | 0 |
| `gt.recipe.fakespaceprojects` | 2 | 0 | 0 | 2 | 0 | 0 |
| `gt.recipe.fermenter` | 45 | 45 | 0 | 0 | 0 | 0 |
| `gt.recipe.fishpond` | 3 | 3 | 0 | 0 | 0 | 0 |
| `gt.recipe.fissionfuel` | 5 | 5 | 0 | 0 | 0 | 0 |
| `gt.recipe.flotationcell` | 12 | 12 | 0 | 0 | 0 | 0 |
| `gt.recipe.fluidchemicaleactor` | 261 | 261 | 0 | 0 | 0 | 0 |
| `gt.recipe.fluidextractor` | 11212 | 11212 | 0 | 0 | 0 | 0 |
| `gt.recipe.fluidheater` | 19 | 19 | 0 | 0 | 0 | 0 |
| `gt.recipe.fluidnaquadahreactor` | 2 | 1 | 0 | 1 | 0 | 0 |
| `gt.recipe.fluidsolidifier` | 28895 | 28895 | 0 | 0 | 0 | 0 |
| `gt.recipe.fog_exotic` | 2 | 0 | 2 | 0 | 2 | 0 |
| `gt.recipe.fog_molten` | 0 | 0 | 0 | 0 | 0 | 0 |
| `gt.recipe.fog_plasma` | 220 | 220 | 0 | 0 | 0 | 0 |
| `gt.recipe.foundry_modules` | 7 | 0 | 7 | 0 | 7 | 0 |
| `gt.recipe.furnace` | 0 | 0 | 0 | 0 | 0 | 0 |
| `gt.recipe.fusionreactor` | 68 | 68 | 0 | 0 | 0 | 0 |
| `gt.recipe.gasturbinefuel` | 28 | 0 | 0 | 28 | 0 | 0 |
| `gt.recipe.hammer` | 13016 | 13016 | 0 | 0 | 0 | 0 |
| `gt.recipe.hugenaquadahreactor` | 2 | 1 | 0 | 1 | 0 | 0 |
| `gt.recipe.ic2nuke` | 33 | 33 | 0 | 0 | 0 | 0 |
| `gt.recipe.icecreammachine` | 105 | 104 | 1 | 0 | 1 | 0 |
| `gt.recipe.implosioncompressor` | 1140 | 1140 | 0 | 0 | 0 | 0 |
| `gt.recipe.industrialchisel` | 3683 | 0 | 3683 | 0 | 3683 | 0 |
| `gt.recipe.industrialcokeoven` | 63 | 63 | 0 | 0 | 0 | 0 |
| `gt.recipe.isotope-decay` | 4 | 4 | 0 | 0 | 0 | 0 |
| `gt.recipe.large_hadron_collider` | 5 | 0 | 0 | 5 | 0 | 0 |
| `gt.recipe.largeboilerfakefuels` | 76 | 1 | 0 | 75 | 1 | 0 |
| `gt.recipe.largechemicalreactor` | 1376 | 1376 | 0 | 0 | 0 | 0 |
| `gt.recipe.largenaquadahreactor` | 2 | 1 | 0 | 1 | 0 | 0 |
| `gt.recipe.laserengraver` | 1424 | 1424 | 0 | 0 | 0 | 0 |
| `gt.recipe.lathe` | 1192 | 1192 | 0 | 0 | 0 | 0 |
| `gt.recipe.lftr` | 3 | 3 | 0 | 0 | 0 | 0 |
| `gt.recipe.lftr.sparging` | 3 | 3 | 0 | 0 | 0 | 0 |
| `gt.recipe.macerator` | 24993 | 24993 | 0 | 0 | 0 | 0 |
| `gt.recipe.magicfuels` | 110 | 6 | 0 | 104 | 0 | 0 |
| `gt.recipe.massfab` | 2 | 0 | 2 | 0 | 2 | 0 |
| `gt.recipe.matterfab2` | 8 | 8 | 0 | 0 | 0 | 0 |
| `gt.recipe.metalbender` | 3784 | 3784 | 0 | 0 | 0 | 0 |
| `gt.recipe.microwave` | 0 | 0 | 0 | 0 | 0 | 0 |
| `gt.recipe.mixer` | 640 | 640 | 0 | 0 | 0 | 0 |
| `gt.recipe.moleculartransformer` | 18 | 18 | 0 | 0 | 0 | 0 |
| `gt.recipe.multiblockrockbreaker` | 8 | 8 | 0 | 0 | 0 | 0 |
| `gt.recipe.multicentrifuge` | 1404 | 1404 | 0 | 0 | 0 | 0 |
| `gt.recipe.multidehydrator` | 88 | 88 | 0 | 0 | 0 | 0 |
| `gt.recipe.multielectro` | 353 | 353 | 0 | 0 | 0 | 0 |
| `gt.recipe.multimixer` | 636 | 636 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.assemblymatrix` | 67 | 40 | 27 | 0 | 27 | 0 |
| `gt.recipe.nanochip.biologicalcoordinator` | 4 | 4 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.boardprocessor` | 14 | 7 | 7 | 0 | 7 | 0 |
| `gt.recipe.nanochip.conversion` | 256 | 256 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.cuttingchamber` | 29 | 29 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.encasementwrapper` | 16 | 16 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.etchingarray` | 9 | 9 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.opticalorganizer` | 8 | 8 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.partprocessor` | 20 | 20 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.superconductorsplitter` | 7 | 7 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanochip.wiretracer` | 21 | 21 | 0 | 0 | 0 | 0 |
| `gt.recipe.nanoforge` | 12 | 12 | 0 | 0 | 0 | 0 |
| `gt.recipe.neutroniumcompressor` | 81 | 81 | 0 | 0 | 0 | 0 |
| `gt.recipe.nuclearsaltprocessingplant` | 8 | 8 | 0 | 0 | 0 | 0 |
| `gt.recipe.oremill` | 948 | 948 | 0 | 0 | 0 | 0 |
| `gt.recipe.orewasher` | 680 | 680 | 0 | 0 | 0 | 0 |
| `gt.recipe.packager` | 3226 | 3226 | 0 | 0 | 0 | 0 |
| `gt.recipe.pcbfactory` | 105 | 105 | 0 | 0 | 0 | 0 |
| `gt.recipe.plasmaforge` | 78 | 78 | 0 | 0 | 0 | 0 |
| `gt.recipe.plasmageneratorfuels` | 113 | 23 | 0 | 90 | 0 | 0 |
| `gt.recipe.polarizer` | 209 | 209 | 0 | 0 | 0 | 0 |
| `gt.recipe.press` | 440 | 440 | 0 | 0 | 0 | 0 |
| `gt.recipe.primitiveblastfurnace` | 418 | 418 | 0 | 0 | 0 | 0 |
| `gt.recipe.printer` | 5 | 5 | 0 | 0 | 0 | 0 |
| `gt.recipe.purificationplantclarifier` | 1 | 1 | 0 | 0 | 0 | 0 |
| `gt.recipe.purificationplantdegasifier` | 1 | 1 | 0 | 0 | 0 | 0 |
| `gt.recipe.purificationplantflocculation` | 2 | 1 | 1 | 0 | 1 | 0 |
| `gt.recipe.purificationplantozonation` | 4 | 4 | 0 | 0 | 0 | 0 |
| `gt.recipe.purificationplantphadjustment` | 2 | 1 | 1 | 0 | 1 | 0 |
| `gt.recipe.purificationplantplasmaheating` | 2 | 1 | 1 | 0 | 1 | 0 |
| `gt.recipe.purificationplantquarkextractor` | 2 | 1 | 1 | 0 | 1 | 0 |
| `gt.recipe.purificationplantuvtreatment` | 2 | 1 | 1 | 0 | 1 | 0 |
| `gt.recipe.pyro` | 47 | 47 | 0 | 0 | 0 | 0 |
| `gt.recipe.quantumcomputer` | 28 | 0 | 0 | 28 | 28 | 0 |
| `gt.recipe.quantumforcesmelter` | 28 | 28 | 0 | 0 | 0 | 0 |
| `gt.recipe.reactorprocessingunit` | 6 | 6 | 0 | 0 | 0 | 0 |
| `gt.recipe.replicator` | 93 | 93 | 0 | 0 | 0 | 0 |
| `gt.recipe.researchStation` | 304 | 0 | 304 | 0 | 304 | 0 |
| `gt.recipe.rockbreaker` | 6 | 0 | 6 | 0 | 6 | 0 |
| `gt.recipe.rocketenginefuel` | 4 | 0 | 0 | 4 | 0 | 0 |
| `gt.recipe.scanner` | 310 | 0 | 310 | 0 | 310 | 0 |
| `gt.recipe.semifluidboilerfuels` | 15 | 0 | 0 | 15 | 0 | 0 |
| `gt.recipe.semifluidgeneratorfuels` | 17 | 0 | 0 | 17 | 0 | 0 |
| `gt.recipe.sifter` | 128 | 128 | 0 | 0 | 0 | 0 |
| `gt.recipe.simplewasher` | 1247 | 1247 | 0 | 0 | 0 | 0 |
| `gt.recipe.smallnaquadahreactor` | 2 | 1 | 0 | 1 | 0 | 0 |
| `gt.recipe.solarfactory` | 54 | 27 | 27 | 0 | 27 | 0 |
| `gt.recipe.solartower` | 1 | 1 | 0 | 0 | 0 | 0 |
| `gt.recipe.spaceAssembler` | 41 | 41 | 0 | 0 | 0 | 0 |
| `gt.recipe.spaceMining` | 232 | 232 | 0 | 0 | 0 | 0 |
| `gt.recipe.spaceResearch` | 0 | 0 | 0 | 0 | 0 | 0 |
| `gt.recipe.thermalboiler` | 4 | 4 | 0 | 0 | 0 | 0 |
| `gt.recipe.thermalcentrifuge` | 693 | 693 | 0 | 0 | 0 | 0 |
| `gt.recipe.thermalgeneratorfuel` | 5 | 0 | 0 | 5 | 0 | 0 |
| `gt.recipe.transcendentplasmamixerrecipes` | 9 | 9 | 0 | 0 | 0 | 0 |
| `gt.recipe.treefarm` | 251 | 0 | 251 | 0 | 251 | 0 |
| `gt.recipe.unpackager` | 387 | 387 | 0 | 0 | 0 | 0 |
| `gt.recipe.upgrade_costs` | 7 | 0 | 7 | 0 | 7 | 0 |
| `gt.recipe.uuamplifier` | 2 | 2 | 0 | 0 | 0 | 0 |
| `gt.recipe.vacfurnace` | 17 | 17 | 0 | 0 | 0 | 0 |
| `gt.recipe.vacuumfreezer` | 317 | 317 | 0 | 0 | 0 | 0 |
| `gt.recipe.wiremill` | 1359 | 1359 | 0 | 0 | 0 | 0 |
| `gtnhlanth.recipe.digester` | 40 | 40 | 0 | 0 | 0 | 0 |
| `gtnhlanth.recipe.disstank` | 33 | 33 | 0 | 0 | 0 | 0 |
| `gtnhlanth.recipe.sc` | 7 | 2 | 0 | 5 | 0 | 0 |
| `gtnhlanth.recipe.tc` | 112 | 112 | 0 | 0 | 0 | 0 |
| `ic.recipe.recycler` | 0 | 0 | 0 | 0 | 0 | 0 |
| `kubatech.defusioncrafter` | 17 | 17 | 0 | 0 | 0 | 0 |
| `kubatech.htgrrecipes` | 9 | 9 | 0 | 0 | 0 | 0 |
