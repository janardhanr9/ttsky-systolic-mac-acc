module systolic_pe (
    input logic [3:0] data_in,
    output logic [3:0] data_out,
    input logic clk,
    input logic rst_n,
    input logic [3:0] weight_in,
    input logic [3:0] bias_in,
    output logic [7:0] acc_out,
    input logic acc_w_en,
    input logic weight_en,
    input logic bias_en
);

    logic signed [7:0] accumulator;
    logic signed [3:0] weight;
    logic signed [3:0] pass_through_data;

    assign acc_out = accumulator;
    assign data_out = pass_through_data;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin 
            accumulator <= 8'b0;
            weight <= 4'b0;
        end else begin
            pass_through_data <= data_in;
            // load
            if (weight_en) begin
                weight <= weight_in;
            end
            if (bias_en) begin
                accumulator <= {{4{bias_in[3]}}, bias_in}; // sign-extend 4-bit bias into 8-bit accumulator
            end
            if (acc_w_en) begin
                logic signed [8:0] mac_result;
                // $signed() is required: data_in is an unsigned port, and a
                // single unsigned operand would make the whole expression
                // unsigned and destroy the sign of the accumulator.
                mac_result = accumulator + ($signed(data_in) * weight);
                // Saturate to 8-bit range
                if (mac_result > 9'sd127)
                    accumulator <= 8'sd127;
                else if (mac_result < -9'sd128)
                    accumulator <= -8'sd128;
                else
                    accumulator <= mac_result[7:0];
            end
        end
    end
endmodule