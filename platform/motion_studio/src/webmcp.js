export function registerPreviewTools(
  context,
  actions,
  signal,
  reportError = () => {},
) {
  if (!context?.registerTool) return [];
  const tools = [
    {
      name: "read_motion_preview",
      title: "Текущее движение в 3D",
      description:
        "Read the local editor selection, pose and preview time. Does not contact a robot.",
      inputSchema: {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute(input) {
        if (
          !input ||
          typeof input !== "object" ||
          Array.isArray(input) ||
          Object.keys(input).length
        )
          throw new Error("Параметры не ожидаются.");
        return actions.read();
      },
    },
    {
      name: "seek_motion_preview",
      title: "Показать позу на шкале",
      description:
        "Pause the local 3D preview and move its playhead to time_ms. Does not edit or save the motion and never controls hardware.",
      inputSchema: {
        type: "object",
        properties: {
          time_ms: { type: "integer", minimum: 0, maximum: 120000 },
        },
        required: ["time_ms"],
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: true },
      execute(input) {
        if (
          !input ||
          typeof input !== "object" ||
          Array.isArray(input) ||
          Object.keys(input).length !== 1 ||
          !Number.isInteger(input.time_ms) ||
          input.time_ms < 0 ||
          input.time_ms > actions.read().duration_ms
        )
          throw new Error("Укажите целое время внутри длительности движения.");
        actions.seek(input.time_ms);
        return actions.read();
      },
    },
  ];
  for (const tool of tools) {
    try {
      Promise.resolve(context.registerTool(tool, { signal })).catch(
        reportError,
      );
    } catch (error) {
      reportError(error);
    }
  }
  return tools.map((t) => t.name);
}
