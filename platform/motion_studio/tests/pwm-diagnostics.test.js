import test from "node:test";
import assert from "node:assert/strict";
import { normalizePwmDiagnostics, pwmDiagnosticText } from "../src/pwm-diagnostics.js";
const id = "arm_positive_x";
const raw = (extra = {}) => ({servos:[{id,available:true,attached:true,pin:12,channel:3,frequency_available:true,freq_hz:50,duty_available:true,duty:819,last_write_available:true,last_write_ok:true,...extra}]});
test("PWM register data never becomes a measured servo angle", () => {
  const d = normalizePwmDiagnostics(raw());
  assert.equal(d.servos[0].duty,819);
  assert.equal(pwmDiagnosticText(d,id), "Канал включён · 50 Гц · регистр PWM 819/8191");
  assert.equal(d.measured_pose, undefined);
});
test("missing and invalid diagnostics stay unavailable, never zero feedback", () => {
  for (const v of [undefined,null,{servos:'invalid'},raw({id:'foreign'}),{servos:[...raw().servos,...raw().servos]}]) {
    assert.equal(normalizePwmDiagnostics(v),null);
  }
  const d=normalizePwmDiagnostics(raw({frequency_available:false,duty:Infinity}));
  assert.equal(d.servos[0].frequency_hz,null);
  assert.equal(d.servos[0].duty,null);
  assert.equal(pwmDiagnosticText(null,id),'Нет данных драйвера');
});
test("detached channel, write failure and wrong frequency remain distinguishable", () => {
  assert.equal(pwmDiagnosticText(normalizePwmDiagnostics(raw({attached:false})),id),'Канал привода отключён');
  assert.match(pwmDiagnosticText(normalizePwmDiagnostics(raw({last_write_ok:false})),id),/не подтвердил запись/);
  assert.match(pwmDiagnosticText(normalizePwmDiagnostics(raw({freq_hz:0})),id),/0 Гц — ожидается 50 Гц/);
});


test("diagnostics follow explicit profile mapping, not physical connector names", () => {
  const d=normalizePwmDiagnostics({servos:[...raw({id:"left_leg",joint_id:"leg_positive_x"}).servos,{id:"left_hand",joint_id:null,available:false,attached:false}]});
  assert.equal(d.servos.length,1);
  assert.equal(d.servos[0].id,"leg_positive_x");
  assert.match(pwmDiagnosticText(d,"leg_positive_x"),/50 Гц/);
  assert.equal(pwmDiagnosticText(d,"leg_negative_x"),"Нет данных драйвера");
});
