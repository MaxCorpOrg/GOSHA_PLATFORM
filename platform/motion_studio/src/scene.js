import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { buildRig, collectMeshes } from "./rig.js";

export function createScene(container, onSelect, onStatus) {
  const scene = new THREE.Scene();
  let dirty = true;
  scene.background = new THREE.Color("#20262e");
  const camera = new THREE.PerspectiveCamera(36, 1, 1, 2000);
  camera.up.set(0, 0, 1);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.domElement.setAttribute(
    "aria-label",
    "3D-модель Гоши. Перетаскивайте для вращения, колесо — масштаб.",
  );
  container.append(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.addEventListener("change", () => {
    dirty = true;
  });
  controls.target.set(0, 0, -27);
  controls.enableDamping = true;
  controls.minDistance = 140;
  controls.maxDistance = 700;
  controls.maxPolarAngle = Math.PI * 0.88;
  const grid = new THREE.GridHelper(600, 30, "#667281", "#333d48");
  grid.rotation.x = Math.PI / 2;
  grid.position.z = -90;
  scene.add(grid);
  scene.add(new THREE.HemisphereLight("#e6efff", "#393546", 2.5));
  const key = new THREE.DirectionalLight("#fff3dc", 4);
  key.position.set(-140, -220, 240);
  scene.add(key);
  const rim = new THREE.DirectionalLight("#adc8f0", 2.2);
  rim.position.set(170, 60, 160);
  scene.add(rim);
  let rig;
  const meshes = [];
  const axes = new THREE.AxesHelper(30);
  axes.visible = false;
  scene.add(axes);
  let currentPose;
  let selected;
  let disposed = false;

  function select(id) {
    dirty = true;
    selected = id;
    for (const mesh of meshes) {
      const active = mesh.userData.joint === id;
      mesh.material.color.set(active ? "#ffba73" : mesh.userData.baseColor);
      mesh.material.emissive.set(active ? "#3a1602" : "#000000");
    }
  }
  const resetView = (front = false) => {
    camera.position.set(front ? 0 : 155, front ? -310 : -260, front ? -25 : 95);
    controls.target.set(0, 0, -27);
    controls.update();
  };
  resetView();

  new GLTFLoader().load(
    `${import.meta.env.BASE_URL}models/gosha.glb`,
    (gltf) => {
      if (disposed) return;
      let named;
      try {
        named = collectMeshes(gltf.scene);
        rig = buildRig(scene, named);
      } catch (error) {
        onStatus(false, error.message);
        return;
      }
      for (const [name, mesh] of named) {
        const baseColor = name.startsWith("foot")
          ? "#687381"
          : name === "body"
            ? "#b8c2ce"
            : "#e5e7e9";
        mesh.material = new THREE.MeshStandardMaterial({
          color: baseColor,
          roughness: 0.54,
          metalness: 0.08,
          side: THREE.DoubleSide,
        });
        mesh.userData.baseColor = baseColor;
        meshes.push(mesh);
      }
      if (currentPose) setPose(currentPose);
      select(selected);
      dirty = true;
      onStatus(true, "Ваша модель · 10 деталей");
    },
    undefined,
    () =>
      onStatus(
        false,
        "Модель не загружена. Подготовьте models/gosha.glb по инструкции README.",
      ),
  );

  function setPose(pose) {
    dirty = true;
    currentPose = pose;
    rig?.setPose(pose);
  }
  const ray = new THREE.Raycaster();
  let down;
  const pointerDown = (e) => {
    down = [e.clientX, e.clientY];
  };
  const pointerUp = (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5)
      return;
    const bounds = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(
      new THREE.Vector2(
        ((e.clientX - bounds.left) / bounds.width) * 2 - 1,
        (-(e.clientY - bounds.top) / bounds.height) * 2 + 1,
      ),
      camera,
    );
    const hit = ray
      .intersectObjects(meshes)
      .find((hit) => hit.object.userData.joint);
    if (hit) onSelect(hit.object.userData.joint);
  };
  renderer.domElement.addEventListener("pointerdown", pointerDown);
  renderer.domElement.addEventListener("pointerup", pointerUp);
  const resize = new ResizeObserver(() => {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    dirty = true;
  });
  resize.observe(container);
  renderer.setAnimationLoop(() => {
    controls.update();
    if (dirty) {
      renderer.render(scene, camera);
      dirty = false;
    }
  });
  return {
    setPose,
    select,
    resetView,
    toggleGrid() {
      grid.visible = !grid.visible;
      dirty = true;
      return grid.visible;
    },
    dispose() {
      disposed = true;
      resize.disconnect();
      renderer.setAnimationLoop(null);
      controls.dispose();
      scene.traverse((o) => {
        o.geometry?.dispose();
        if (o.material) o.material.dispose();
      });
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
