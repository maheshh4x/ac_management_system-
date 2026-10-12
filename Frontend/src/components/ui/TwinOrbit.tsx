import { cn } from "@/lib/utils";

function TwinOrbit({
  className,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <>
      <style>{`
        @keyframes loading-ui-twin-orbit-rotate {
          100% {
            transform: rotate(360deg) translate(155%);
          }
        }
      `}</style>

      <span
        role="status"
        aria-label="Loading"
        className={cn(
          "relative inline-block aspect-square rounded-full bg-current",
          className
        )}
        style={
          {
            "--duration": "0.8s",
          } as React.CSSProperties
        }
        {...props}
      >
        <span
          aria-hidden="true"
          className="absolute inset-0 rounded-full bg-current"
          style={{
            transform: "rotate(0deg) translate(155%)",
            animation:
              "loading-ui-twin-orbit-rotate var(--duration) ease-in-out infinite",
          }}
        />

        <span
          aria-hidden="true"
          className="absolute inset-0 rounded-full bg-current"
          style={{
            transform: "rotate(0deg) translate(155%)",
            animation:
              "loading-ui-twin-orbit-rotate var(--duration) ease-in-out infinite",
            animationDelay: "calc(var(--duration) / -2)",
          }}
        />

        <span className="sr-only">Loading</span>
      </span>
    </>
  );
}

export { TwinOrbit };
