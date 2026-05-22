import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setConcurrency(1);
Config.setOverwriteOutput(true);
// Allow file:// access for local input video.
Config.setChromiumOpenGlRenderer("angle");
