import { describe, expect, it } from "vitest";
import { cameraLayerTransform, cameraPointerDelta, projectWorldPoint } from "./canvasCamera";
import type { CameraState } from "./types";

const center: CameraState = { center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: 0.05 };

describe("semantic camera viewport projection", () => {
  it("is identity at the canonical centered camera", () => {
    expect(cameraLayerTransform(center)).toContain("scale(1)");
    expect(projectWorldPoint(center, 160, 280)).toEqual({ x: 160, y: 280 });
    expect(cameraPointerDelta(center, 80, 54, 960, 540)).toEqual({ x: 160, y: 108 });
  });

  it("maintains world coordinates at a panned, zoomed and rotated camera", () => {
    const camera = { ...center, center_x: 1200, center_y: 630, zoom: 2, rotation_deg: 90 };
    expect(projectWorldPoint(camera, camera.center_x, camera.center_y)).toEqual({ x: 960, y: 540 });
    const world = cameraPointerDelta(camera, 100, 0, 960, 540);
    expect(world?.x).toBeCloseTo(0, 8);
    expect(world?.y).toBeCloseTo(100, 8);
    const origin = projectWorldPoint(camera, 1200, 630)!;
    const moved = projectWorldPoint(camera, 1200 + world!.x, 630 + world!.y)!;
    expect(moved.x - origin.x).toBeCloseTo(200, 8);
    expect(moved.y - origin.y).toBeCloseTo(0, 8);
  });

  it("safe margins remain a frame overlay independent of zoom", () => {
    const camera = { ...center, zoom: 3, safe_margin: 0.2 };
    expect(cameraLayerTransform(camera)).toContain("scale(3)");
    expect(projectWorldPoint(camera, 960 + 100, 540)?.x).toBeCloseTo(1260);
    expect(cameraPointerDelta(camera, 30, 0, 960, 540)?.x).toBeCloseTo(20);
  });

  it("fails closed for nonfinite values and zero stage dimensions", () => {
    expect(cameraPointerDelta(center, 10, 5, 0, 100)).toBeNull();
    expect(cameraPointerDelta({ ...center, zoom: 0 }, 10, 5, 500, 300)).toBeNull();
    expect(cameraPointerDelta({ ...center, rotation_deg: Number.NaN }, 10, 5, 500, 300)).toBeNull();
    expect(projectWorldPoint({ ...center, zoom: Infinity }, 5, 6)).toBeNull();
    expect(cameraLayerTransform({ ...center, zoom: Number.NaN })).toBe("none");
  });
});


import { cameraSvgTransform } from "./canvasCamera";
import { cameraReframePose } from "./cameraReframe";
// Independent CAM-30F-24D rational oracle from the A4 source contract.
// Analytic source expectations; these arrays are not rendered pixel evidence.
const cameraOracle = [{"name":"original","reframe":{"id":"30000000-0000-4000-8000-000000000001","scene_id":"20000000-0000-4000-8000-000000000001","component_version":2,"start_center_x":960,"start_center_y":540,"end_center_x":1000,"end_center_y":520,"duration_frames":24},"samples":[[0,"960/1","540/1","140/1","90/1","375/1","180/1"],[1,"2885/3","3235/6","1255/9","1625/18","3370/9","3245/18"],[2,"2890/3","1615/3","1250/9","815/9","3365/9","1625/9"],[3,"965/1","1075/2","415/3","545/6","1120/3","1085/6"],[4,"2900/3","1610/3","1240/9","820/9","3355/9","1630/9"],[5,"2905/3","3215/6","1235/9","1645/18","3350/9","3265/18"],[6,"970/1","535/1","410/3","275/3","1115/3","545/3"],[7,"2915/3","3205/6","1225/9","1655/18","3340/9","3275/18"],[8,"2920/3","1600/3","1220/9","830/9","3335/9","1640/9"],[9,"975/1","1065/2","135/1","185/2","370/1","365/2"],[10,"2930/3","1595/3","1210/9","835/9","3325/9","1645/9"],[11,"2935/3","3185/6","1205/9","1675/18","3320/9","3295/18"],[12,"980/1","530/1","400/3","280/3","1105/3","550/3"],[13,"2945/3","3175/6","1195/9","1685/18","3310/9","3305/18"],[14,"2950/3","1585/3","1190/9","845/9","3305/9","1655/9"],[15,"985/1","1055/2","395/3","565/6","1100/3","1105/6"],[16,"2960/3","1580/3","1180/9","850/9","3295/9","1660/9"],[17,"2965/3","3155/6","1175/9","1705/18","3290/9","3325/18"],[18,"990/1","525/1","130/1","95/1","365/1","185/1"],[19,"2975/3","3145/6","1165/9","1715/18","3280/9","3335/18"],[20,"2980/3","1570/3","1160/9","860/9","3275/9","1670/9"],[21,"995/1","1045/2","385/3","575/6","1090/3","1115/6"],[22,"2990/3","1565/3","1150/9","865/9","3265/9","1675/9"],[23,"2995/3","3125/6","1145/9","1735/18","3260/9","3355/18"],[24,"1000/1","520/1","380/3","290/3","1085/3","560/3"],[25,"1000/1","520/1","380/3","290/3","1085/3","560/3"],[26,"1000/1","520/1","380/3","290/3","1085/3","560/3"],[27,"1000/1","520/1","380/3","290/3","1085/3","560/3"],[28,"1000/1","520/1","380/3","290/3","1085/3","560/3"],[29,"1000/1","520/1","380/3","290/3","1085/3","560/3"]]},{"name":"edited","reframe":{"id":"30000000-0000-4000-8000-000000000001","scene_id":"20000000-0000-4000-8000-000000000001","component_version":2,"start_center_x":960,"start_center_y":540,"end_center_x":1012,"end_center_y":520,"duration_frames":24},"samples":[[0,"960/1","540/1","140/1","90/1","375/1","180/1"],[1,"5773/6","3235/6","2507/18","1625/18","6737/18","3245/18"],[2,"2893/3","1615/3","1247/9","815/9","3362/9","1625/9"],[3,"1933/2","1075/2","827/6","545/6","2237/6","1085/6"],[4,"2906/3","1610/3","1234/9","820/9","3349/9","1630/9"],[5,"5825/6","3215/6","2455/18","1645/18","6685/18","3265/18"],[6,"973/1","535/1","407/3","275/3","1112/3","545/3"],[7,"5851/6","3205/6","2429/18","1655/18","6659/18","3275/18"],[8,"2932/3","1600/3","1208/9","830/9","3323/9","1640/9"],[9,"1959/2","1065/2","267/2","185/2","737/2","365/2"],[10,"2945/3","1595/3","1195/9","835/9","3310/9","1645/9"],[11,"5903/6","3185/6","2377/18","1675/18","6607/18","3295/18"],[12,"986/1","530/1","394/3","280/3","1099/3","550/3"],[13,"5929/6","3175/6","2351/18","1685/18","6581/18","3305/18"],[14,"2971/3","1585/3","1169/9","845/9","3284/9","1655/9"],[15,"1985/2","1055/2","775/6","565/6","2185/6","1105/6"],[16,"2984/3","1580/3","1156/9","850/9","3271/9","1660/9"],[17,"5981/6","3155/6","2299/18","1705/18","6529/18","3325/18"],[18,"999/1","525/1","127/1","95/1","362/1","185/1"],[19,"6007/6","3145/6","2273/18","1715/18","6503/18","3335/18"],[20,"3010/3","1570/3","1130/9","860/9","3245/9","1670/9"],[21,"2011/2","1045/2","749/6","575/6","2159/6","1115/6"],[22,"3023/3","1565/3","1117/9","865/9","3232/9","1675/9"],[23,"6059/6","3125/6","2221/18","1735/18","6451/18","3355/18"],[24,"1012/1","520/1","368/3","290/3","1073/3","560/3"],[25,"1012/1","520/1","368/3","290/3","1073/3","560/3"],[26,"1012/1","520/1","368/3","290/3","1073/3","560/3"],[27,"1012/1","520/1","368/3","290/3","1073/3","560/3"],[28,"1012/1","520/1","368/3","290/3","1073/3","560/3"],[29,"1012/1","520/1","368/3","290/3","1073/3","560/3"]]}];

function fraction(value:string|number):number {
  const [n,d]=String(value).split("/").map(Number);return n/(d??1);
}
describe("single camera viewport with unchanged project objects",()=>{
  it("matches all 30 original and 30 edited exact-time analytic samples",()=>{
    for(const variant of cameraOracle){
      for(const row of variant.samples){
        const [index,cx,cy,rx,ry,gx,gy]=row.map(fraction);
        const pose=cameraReframePose(variant.reframe,index/30)!;
        expect(pose.center_x).toBeCloseTo(cx,11);expect(pose.center_y).toBeCloseTo(cy,11);
        const matrix=cameraSvgTransform(pose)!.slice(7,-1).split(" ").map(Number);
        expect(matrix.slice(0,4)).toEqual([1,0,0,1]);
        for(const [x,y,expectedX,expectedY] of [[420,270,rx,ry],[1125,540,gx,gy]]){
          expect((matrix[0]*x+matrix[2]*y+matrix[4])/3).toBeCloseTo(expectedX,10);
          expect((matrix[1]*x+matrix[3]*y+matrix[5])/3).toBeCloseTo(expectedY,10);
          const point=projectWorldPoint(pose,x,y)!;
          expect(point.x/3).toBeCloseTo(expectedX,10);expect(point.y/3).toBeCloseTo(expectedY,10);
        }
      }
    }
  });
  it("has no half-frame offset, uses frame D as endpoint, and holds afterward",()=>{
    const move=cameraOracle[0].reframe;
    expect(cameraReframePose(move,0)!.center_x).toBe(move.start_center_x);
    expect(cameraReframePose(move,23/30)!.center_x).toBeLessThan(move.end_center_x);
    expect(cameraReframePose(move,24/30)!.center_x).toBe(move.end_center_x);
    expect(cameraReframePose(move,29/30)!.center_x).toBe(move.end_center_x);
    // A 24-frame scene ends at sample 23; the editable endpoint is separate.
    const frames=Array.from({length:24},(_,i)=>cameraReframePose(move,i/30));
    expect(frames).toHaveLength(24);expect(frames.at(-1)!.center_x).not.toBe(move.end_center_x);
  });
  it("matches the Canvas camera projection for rotation and zoom, rejecting non-finite poses",()=>{
    const pose={center_x:1012,center_y:520,zoom:1.5,rotation_deg:90,safe_margin:.1};
    const matrix=cameraSvgTransform(pose)!.slice(7,-1).split(" ").map(Number);
    const world={x:420,y:270},before={...world};
    const point=projectWorldPoint(pose,world.x,world.y)!;
    expect(matrix[0]*world.x+matrix[2]*world.y+matrix[4]).toBeCloseTo(point.x,10);
    expect(matrix[1]*world.x+matrix[3]*world.y+matrix[5]).toBeCloseTo(point.y,10);
    expect(world).toEqual(before);
    expect(cameraSvgTransform({...pose,zoom:0})).toBeNull();
    expect(cameraSvgTransform({...pose,center_x:NaN})).toBeNull();
  });
});
