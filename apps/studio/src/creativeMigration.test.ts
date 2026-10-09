import { expect, it } from "vitest";
import { applyChange, bootstrap } from "./api";
import { fixtureProject } from "./fixture";
import { defaultHeroConfig, emptyProductionDesign } from "./creativeProduction";

it("upgrades an old project only when a write succeeds without changing generation",async()=>{
  const old=structuredClone(fixtureProject);old.schema_version=1;delete old.production_design;
  const saved=await bootstrap();
  await expect(applyChange(old,{type:"undo_creative_patch",patch_id:crypto.randomUUID()})).rejects.toThrow();
  expect(await bootstrap()).toEqual(saved);expect(old.schema_version).toBe(1);
  const upgraded=await applyChange(old,{type:"rename_project",title:"Updated legacy project"});
  expect(upgraded.schema_version).toBe(3);expect(upgraded.revision).toBe(old.revision+1);expect(upgraded.generation).toBe(old.generation);
  expect(upgraded.scenes).toEqual(old.scenes);
});

it("rejects mismatched and unknown project formats instead of dropping creative fields",async()=>{
  let project=structuredClone(fixtureProject);project.schema_version=1;project.locks=[];project.production_design=emptyProductionDesign();
  project=await applyChange(project,{type:"upsert_product_hero",instance_id:crypto.randomUUID(),scene_id:project.scenes[0].id,config:defaultHeroConfig()});
  expect(project.schema_version).toBe(3);
  const mislabeled={...project,schema_version:1};
  await expect(applyChange(mislabeled,{type:"rename_project",title:"Do not hide the new state"})).rejects.toThrow("schema 2");
  await expect(applyChange({...project,schema_version:4},{type:"rename_project",title:"Unknown future schema"})).rejects.toThrow("Unsupported");
});
