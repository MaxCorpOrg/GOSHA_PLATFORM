import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { buildRig, collectMeshes } from "./rig.js";

export function createScene(container, onSelect, onStatus) {
  const scene = new THREE.Scene();
  let dirty = true;
  scene.background = new THREE.Color("#20262e");
  let camera = new THREE.PerspectiveCamera(
    36, container.clientWidth / container.clientHeight, 1, 2000,
  );
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
  const robotRoot = new THREE.Group();
  scene.add(robotRoot);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(3000, 3000),
    new THREE.MeshStandardMaterial({ color: "#252d34", roughness: .9, side: THREE.DoubleSide }));
  floor.position.z = -90.1;
  floor.visible = false;
  scene.add(floor);
  const contacts = new THREE.Group();
  scene.add(contacts);
  const contactMaterial = new THREE.MeshBasicMaterial({ color: "#8ce3bb", depthTest: false });
  const contactGeometry = new THREE.SphereGeometry(1.3, 10, 8);
  for (let i = 0; i < 24; i++) {
    const marker = new THREE.Mesh(contactGeometry, contactMaterial);
    marker.visible = false;
    marker.renderOrder = 2;
    contacts.add(marker);
  }
  let physicsFrame;
  let physicsEnabled = false;
  let preferredView = false;
  let orthoSize = 260;
  scene.add(new THREE.HemisphereLight("#e6efff", "#393546", 2.5));
  const key = new THREE.DirectionalLight("#fff3dc", 4);
  key.position.set(-140, -220, 240);
  scene.add(key);
  const rim = new THREE.DirectionalLight("#adc8f0", 2.2);
  rim.position.set(170, 60, 160);
  scene.add(rim);
  let rig;
  let modelBounds;
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
    preferredView = front;
    const sphere = modelBounds?.getBoundingSphere(new THREE.Sphere())
      ?? new THREE.Sphere(new THREE.Vector3(0, 0, -27), 119);
    const aspect = container.clientWidth / container.clientHeight;
    if (front === "isometric") {
      orthoSize = sphere.radius * 2.35 / Math.min(1, aspect);
      camera = new THREE.OrthographicCamera(-orthoSize*aspect/2, orthoSize*aspect/2, orthoSize/2, -orthoSize/2, 1, 3000);
    } else if (camera.isOrthographicCamera) {
      camera = new THREE.PerspectiveCamera(36, aspect, 1, 2000);
    }
    camera.up.set(0, 0, 1);
    controls.object = camera;
    let distance = 400;
    if (!camera.isOrthographicCamera) {
      const halfFov = THREE.MathUtils.degToRad(camera.fov / 2);
      const limitingFov = Math.min(halfFov, Math.atan(Math.tan(halfFov) * camera.aspect));
      distance = Math.min(controls.maxDistance,
        Math.max(controls.minDistance, sphere.radius / Math.sin(limitingFov) * 1.08));
    }
    const direction = new THREE.Vector3(...(front === "isometric" ? [1, -1, 1] : front ? [0, -1, 0] : [155, -260, 122])).normalize();
    controls.target.copy(sphere.center);
    camera.position.copy(sphere.center).addScaledVector(direction, distance);
    controls.update();
    dirty = true;
  };
  resetView();

  function attachModel(named, label) {
    rig = buildRig(robotRoot, named);
    scene.updateMatrixWorld(true);
    modelBounds = new THREE.Box3();
    for (const mesh of named.values()) modelBounds.expandByObject(mesh);
    for (const [name, mesh] of named) {
      const hasSourceColours = Boolean(mesh.geometry.getAttribute("color"));
      const baseColor = hasSourceColours ? "#ffffff" : name.startsWith("foot")
        ? "#687381"
        : ["button_boot", "power_switch"].includes(name)
          ? "#323943"
        : name === "body"
          ? "#b8c2ce"
          : "#e5e7e9";
      mesh.material = new THREE.MeshStandardMaterial({
        color: baseColor,
        vertexColors: hasSourceColours,
        roughness: 0.54,
        metalness: hasSourceColours ? 0.25 : 0.08,
        side: THREE.DoubleSide,
      });
      mesh.userData.baseColor = baseColor;
      meshes.push(mesh);
    }
    if (currentPose) setPose(currentPose);
    select(selected);
    resetView(preferredView);
    if (physicsEnabled) setPhysicsFrame(physicsFrame);
    dirty = true;
    onStatus(true, label);
  }
  const modelUnavailable = () => {
    if (disposed) return;
    onStatus(false, "Полная модель Гоши недоступна. Восстановите модель и перезапустите Motion Studio.");
  };
  new GLTFLoader().load(
    // The authenticated panel route serves only this model filename. Keep the
    // route stable; the source digest invalidates caches for the new assembly.
    `${import.meta.env.BASE_URL}models/gosha.glb?v=208bb99d`,
    (gltf) => {
      if (disposed) return;
      try {
        const named = collectMeshes(gltf.scene);
        attachModel(named, `Полная модель Гоши · ${named.size} деталей`);
      } catch (error) {
        modelUnavailable();
      }
    },
    undefined,
    modelUnavailable,
  );

  function setPose(pose) {
    dirty = true;
    currentPose = pose;
    rig?.setPose(pose);
  }
  function setPhysicsFrame(frame) {
    physicsEnabled = true;
    physicsFrame = frame;
    floor.visible = true;
    robotRoot.position.set(...(frame?.base.position_m ?? [0, 0, 0]).map(n => n*1000));
    robotRoot.quaternion.set(...(frame?.base.quaternion_xyzw ?? [0, 0, 0, 1]));
    contacts.children.forEach((marker, i) => {
      const contact = frame?.contacts[i];
      marker.visible = Boolean(contact);
      if (contact) marker.position.set(...contact.position_m.map(n => n*1000));
    });
    dirty = true;
  }
  function clearPhysicsFrame() {
    if (!physicsEnabled) return;
    physicsEnabled = false;
    physicsFrame = undefined;
    robotRoot.position.set(0, 0, 0);
    robotRoot.quaternion.identity();
    floor.visible = false;
    contacts.children.forEach(marker => { marker.visible = false; });
    dirty = true;
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
    if (camera.isOrthographicCamera) {
      camera.left = -orthoSize*(w/h)/2;
      camera.right = orthoSize*(w/h)/2;
      camera.top = orthoSize/2;
      camera.bottom = -orthoSize/2;
    } else camera.aspect = w / h;
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
    setPhysicsFrame,
    clearPhysicsFrame,
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
