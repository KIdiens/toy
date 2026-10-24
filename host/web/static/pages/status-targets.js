/* 只显示本角色的新鲜指令目标；没有实测位置反馈。 */
export function createTargetDisplay(axes, { now = () => performance.now(),
  schedule = setTimeout, cancel = clearTimeout } = {}) {
  let timer = null;
  function clear() {
    if (timer !== null) cancel(timer);
    timer = null;
    axes.forEach(el => { el.frame = null; });
  }
  function update(status, receivedAt = now()) {
    clear();
    const limits = status?.axis_limits;
    const validLimits = limits && limits.role_id === status.role_id &&
      typeof limits.config_version === 'string' && limits.config_version.length > 0 &&
      [limits.min_us, limits.max_us, limits.neutral_us].every(Number.isInteger) &&
      limits.min_us >= 1400 && limits.max_us <= 1600 && limits.min_us < limits.max_us &&
      limits.neutral_us >= limits.min_us && limits.neutral_us <= limits.max_us;
    axes.forEach(el => {
      for (const [attr, key] of [['min-us','min_us'], ['max-us','max_us'], ['neutral-us','neutral_us']]) {
        if (validLimits) el.setAttribute(attr, limits[key]);
        else el.removeAttribute(attr);
      }
    });
    const target = status?.target;
    const remaining = target?.valid_for_ms - Math.max(0, now() - receivedAt);
    if (!validLimits || status.state !== 'PERFORMING' || status.fault ||
        target?.source !== 'sent_frame' || target.role_id !== status.role_id ||
        target.config_version !== limits.config_version ||
        !Number.isInteger(target.valid_for_ms) || target.valid_for_ms > 1000 || remaining <= 0 ||
        !Array.isArray(target.target_us) || target.target_us.length !== 4 ||
        !target.target_us.every(v => Number.isInteger(v) && v >= limits.min_us && v <= limits.max_us)) return;
    axes.forEach(el => { el.frame = [...target.target_us]; });
    timer = schedule(clear, remaining);
  }
  return { update, clear };
}
