import { redirect } from "next/navigation";

// 루트 = 레이더 (시스템의 핵심 화면). 구 매수후보 대시보드는 /candidates 로 이동.
export default function Home() {
  redirect("/radar");
}
